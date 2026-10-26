"""Sample K candidate edits per test prompt from the base model or base + adapter."""
import argparse, gzip, json, time
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, StoppingCriteriaList, set_seed
from peft import PeftModel

BASE = 'Qwen/Qwen2.5-Coder-7B-Instruct'

def open_data(path):
    path = Path(path)
    return gzip.open(path, 'rt') if path.suffix == '.gz' else path.open()



class DegenerateStop(StoppingCriteria):
    """Per-sequence stop when the last `window` tokens use <= `max_unique` distinct ids (runaway repetition)."""
    def __init__(self, prompt_len, window=64, max_unique=2):
        self.p, self.w, self.k = prompt_len, window, max_unique

    def __call__(self, input_ids, scores, **kw):
        if input_ids.shape[1] - self.p < self.w:
            return torch.zeros(input_ids.shape[0], dtype=torch.bool, device=input_ids.device)
        tail = input_ids[:, -self.w:]
        uniq = torch.tensor([len(torch.unique(t)) for t in tail], device=input_ids.device)
        return uniq <= self.k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--adapter', default=None, help='adapter dir; omit for the base model')
    ap.add_argument('--label', required=True)
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--k', type=int, default=4)
    ap.add_argument('--temperature', type=float, default=0.7)
    ap.add_argument('--max-new', type=int, default=2560)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--merge', action='store_true', help='merge adapter into weights (in fp32, then cast to bf16)')
    ap.add_argument('--format-hint', default=None, help='train.jsonl path: add exact block syntax and one train example to the system message')
    a = ap.parse_args()
    set_seed(a.seed)
    tok = AutoTokenizer.from_pretrained(BASE)
    if a.adapter and a.merge:
        # fp32 merge done on the GPU (about 31 GB peak) so the job needs little host RAM.
        model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float32, device_map='cuda')
        model = PeftModel.from_pretrained(model, a.adapter).merge_and_unload().to(torch.bfloat16)
    else:
        model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16).cuda()
        if a.adapter: model = PeftModel.from_pretrained(model, a.adapter)
    model.eval()
    rows = [json.loads(l) for l in open_data(a.data)][:a.limit]
    if a.format_hint:
        # Shortest train example as a worked demonstration of the exact output format.
        ex = min((json.loads(l) for l in open_data(a.format_hint)), key=lambda r: r['n_tokens'])
        hint = ('\n\nOutput format, exactly:\nHypothesis: <one line>\n\n<<<<<<< SEARCH <path>\n<exact existing lines>\n=======\n<new lines>\n>>>>>>> REPLACE\n'
                'The SEARCH text must match the shown code exactly and occur once in the file. Use paths as shown after "// FILE:". '
                'No other text.\n\nExample input:\n' + ex['messages'][1]['content'] + '\n\nExample output:\n' + ex['messages'][2]['content'])
        for r in rows: r['messages'][0] = {'role': 'system', 'content': r['messages'][0]['content'] + hint}
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists(): done = {json.loads(l)['id'] for l in open(out)}
    with open(out, 'a') as fh:
        for r in rows:
            if r['id'] in done: continue
            prompt_msgs = r['messages'][:-1] if r['messages'][-1]['role'] == 'assistant' else r['messages']   # strip only a target turn
            ids = tok.apply_chat_template(prompt_msgs, add_generation_prompt=True, return_tensors='pt').cuda()
            t0 = time.time()
            with torch.no_grad(), torch.autocast('cuda', dtype=torch.bfloat16):
                g = model.generate(ids, do_sample=True, temperature=a.temperature, top_p=0.95, max_new_tokens=a.max_new,
                                   num_return_sequences=a.k, use_cache=True, pad_token_id=tok.eos_token_id,
                                   attention_mask=torch.ones_like(ids),
                                   stopping_criteria=StoppingCriteriaList([DegenerateStop(ids.shape[1])]))
            cands = [tok.decode(s[ids.shape[1]:], skip_special_tokens=True) for s in g]
            fh.write(json.dumps({'id': r['id'], 'parent': r['parent'], 'label': a.label, 'candidates': cands,
                                 'seconds': time.time() - t0}) + '\n'); fh.flush()
            print(r['id'][:8], f'{time.time() - t0:.0f}s', flush=True)


if __name__ == '__main__':
    main()
