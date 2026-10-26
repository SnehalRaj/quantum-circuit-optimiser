import unittest
from llm_circuit_optimiser.showcase import control, assess, task_prompt, freeze_rules

TASK={'id':'a','wires':3,'before':[['CCX',0,1,2],['X',1],['CCX',0,1,2]],'occurrences':2}

class ShowcaseTests(unittest.TestCase):
    def test_controls_count_every_slot_and_reproduce(self):
        for arm in ['random','evolution','enumeration']:
            a=control(TASK,arm,401,16)
            self.assertEqual(len(a['attempts']),16)
            b=control(TASK,arm,401,16)
            self.assertEqual(a['attempts'],b['attempts'])

    def test_invalid_proposal_stays_in_results(self):
        r=assess(TASK,'H 0','grammar')
        self.assertFalse(r['accepted'])
        self.assertIn('error',r)

    def test_forms_share_task_information(self):
        a=task_prompt(TASK,'grammar',[]);b=task_prompt(TASK,'source',[])
        self.assertEqual(a['task'],b['task'])
        self.assertEqual(a['gate_semantics'],b['gate_semantics'])

    def test_freezes_best_proved_rule(self):
        yes=assess(TASK,'CX 0 2; X 1','grammar')
        no=assess(TASK,'','grammar')
        rules=freeze_rules([TASK],{'a':[yes,no]})
        self.assertEqual(len(rules),1)
        self.assertEqual(rules[0]['after'],[['CX',0,2],['X',1]])
