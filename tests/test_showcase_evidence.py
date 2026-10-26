import importlib.util
from pathlib import Path
import tempfile
import unittest
from llm_circuit_optimiser.common import digest, save
from llm_circuit_optimiser.proposal_calls import SCHEMA
from llm_circuit_optimiser.showcase import assess, task_prompt

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('showcase_driver',ROOT/'studies/harness-showcase-20261026/run.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
TASK={'id':'a','wires':3,'before':[['CCX',0,1,2],['X',1],['CCX',0,1,2]]}

class EvidenceTests(unittest.TestCase):
    def fixture(self,out):
        p={'tasks':[TASK],'model_config':{'provider':'claude','model':'model'},'batch_size':8,'rounds':2}
        folder=out/'calls/grammar-401-a-0'
        prompt=task_prompt(TASK,'grammar',[]);answer={'proposals':['CX 0 2; X 1']}
        receipt={'success':True,'request_hash':digest({'prompt':prompt,'config':p['model_config'],'schema':SCHEMA}),
                 'answer_hash':digest(answer),'usage':{'input_tokens':123,'output_tokens':45}}
        attempts=[assess(TASK,s,'grammar') for s in answer['proposals']]
        attempts += [{'accepted':False,'error':'unused or failed proposal slot','form':'grammar'}]*7
        row={'form':'grammar','seed':401,'task':'a','round':0,'call_index':0,'attempts':attempts,'receipt':receipt}
        for name,data in [('prompt',prompt),('answer',answer),('receipt',receipt),('evaluated',row)]:save(folder/f'{name}.json',data)
        return p,folder,row

    def test_valid_saved_call(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);p,_,row=self.fixture(out)
            self.assertEqual(driver.saved_call(out,p,'grammar',401,TASK,0,0,[]),row)

    def test_changed_valid_proposal_is_not_model_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);p,folder,row=self.fixture(out)
            row['attempts'][0]=assess(TASK,'X 1; CX 0 2','grammar')
            self.assertTrue(row['attempts'][0]['accepted'])
            save(folder/'evaluated.json',row)
            with self.assertRaisesRegex(ValueError,'model answer'):
                driver.saved_call(out,p,'grammar',401,TASK,0,0,[])

    def test_missing_calls_block_complete_verification(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);p,_,_=self.fixture(out)
            with self.assertRaises(FileNotFoundError):driver.validate_calls(out,p)

    def test_changed_usage_blocks_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);p,folder,row=self.fixture(out)
            row['receipt']['usage']['output_tokens']=0;save(folder/'evaluated.json',row)
            with self.assertRaisesRegex(ValueError,'usage'):
                driver.saved_call(out,p,'grammar',401,TASK,0,0,[])
