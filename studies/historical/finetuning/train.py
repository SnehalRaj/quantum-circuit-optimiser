"""SFT of Qwen2.5-Coder-7B-Instruct on ecdsa-edits with LoRA / OFT / QuIC adapters (loss on assistant tokens only)."""
import argparse, gzip, json, math, os, time
from pathlib import Path
import torch
from torch.utils.data import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments, set_seed
from peft import get_peft_model, LoraConfig, OFTConfig
try:
    from peft import CompoundConfig
except ImportError:
    def CompoundConfig(**kwargs):
        raise ImportError("QuIC needs the original PEFT CompoundConfig extension. LoRA modes use standard PEFT.")

def open_data(path):
    path = Path(path)
    if not path.exists() and Path(str(path) + '.gz').exists():
        path = Path(str(path) + '.gz')
    return gzip.open(path, 'rt') if path.suffix == '.gz' else path.open()


BASE = 'Qwen/Qwen2.5-Coder-7B-Instruct'
T = ['q_proj', 'k_proj', 'v_proj', 'o_proj']
QUIC = dict(r=3, block_share=False, use_orthogonal=True, compound_pattern=['comp_1', 'comp_2', 'comp_3'],
            compound_type='comp', adapter_multiplicative=True, target_modules=T, task_type='CAUSAL_LM')
METHODS = {
    'lora':   lambda: LoraConfig(r=1, lora_alpha=2, lora_dropout=0.0, target_modules=T, task_type='CAUSAL_LM'),
    'lora16': lambda: LoraConfig(r=16, lora_alpha=32, lora_dropout=0.0, target_modules=T, task_type='CAUSAL_LM'),
    'oft':    lambda: OFTConfig(r=896, module_dropout=0.0, target_modules=T, task_type='CAUSAL_LM'),
    'quic_m': lambda: CompoundConfig(num_adapters=8, use_offset_blocks=True, **QUIC),
    'quic_s': lambda: CompoundConfig(num_adapters=1, use_offset_blocks=False, **QUIC),
}


class Chat(Dataset):
    def __init__(self, path, tok, max_len, limit=None):
        self.items = []
        for line in open_data(path):
            msgs = json.loads(line)['messages']
            prompt = tok.apply_chat_template(msgs[:-1], tokenize=True, add_generation_prompt=True)
            full = tok.apply_chat_template(msgs, tokenize=True)
            assert full[:len(prompt)] == prompt, 'chat template prefix mismatch'
            full = full[:max_len]
            labels = [-100] * len(prompt) + full[len(prompt):]
            self.items.append((full, labels[:len(full)]))
            if limit and len(self.items) >= limit: break

    def __len__(self): return len(self.items)
    def __getitem__(self, i): return self.items[i]


def collate(batch, pad_id):
    n = max(len(x) for x, _ in batch)
    ids = torch.full((len(batch), n), pad_id); lab = torch.full((len(batch), n), -100); att = torch.zeros((len(batch), n), dtype=torch.long)
    for i, (x, y) in enumerate(batch):
        ids[i, :len(x)] = torch.tensor(x); lab[i, :len(y)] = torch.tensor(y); att[i, :len(x)] = 1
    return {'input_ids': ids, 'labels': lab, 'attention_mask': att}


@torch.no_grad()
def nll(model, ds, pad_id):
    """Token-weighted mean NLL over target tokens."""
    model.eval(); tot, cnt = 0.0, 0
    for x, y in ds.items:
        b = collate([(x, y)], pad_id); b = {k: v.to(model.device) for k, v in b.items()}
        with torch.autocast('cuda', dtype=torch.bfloat16):
            logits = model(input_ids=b['input_ids'], attention_mask=b['attention_mask']).logits[:, :-1].float()
        tgt = b['labels'][:, 1:]
        loss = torch.nn.functional.cross_entropy(logits.reshape(-1, logits.size(-1)), tgt.reshape(-1), ignore_index=-100, reduction='sum')
        tot += loss.item(); cnt += (tgt != -100).sum().item()
    model.train()
    return tot / cnt, cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--method', required=True, choices=list(METHODS) + ['base'])
    ap.add_argument('--lr', type=float, default=2e-4)
    ap.add_argument('--epochs', type=float, default=3)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--max-len', type=int, default=8192)
    ap.add_argument('--grad-accum', type=int, default=16)
    ap.add_argument('--max-steps', type=int, default=-1)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--init-adapter', default=None, help='continue training an existing PEFT adapter')
    a = ap.parse_args()
    set_seed(a.seed); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(BASE); pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    train = Chat(f'{a.data}/train.jsonl', tok, a.max_len, a.limit)
    val = Chat(f'{a.data}/val.jsonl', tok, a.max_len, a.limit)
    test = Chat(f'{a.data}/test.jsonl', tok, a.max_len, a.limit)
    model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16, attn_implementation='sdpa').cuda()
    model.config.use_cache = False
    res = {'method': a.method, 'lr': a.lr, 'epochs': a.epochs, 'seed': a.seed, 'n_train': len(train)}
    if a.method == 'base':
        res['val_nll'], res['val_tokens'] = nll(model, val, pad); res['test_nll'], res['test_tokens'] = nll(model, test, pad)
        (out / 'result.json').write_text(json.dumps(res, indent=2)); print(json.dumps(res)); return
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.enable_input_require_grads()
    if a.init_adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, a.init_adapter, is_trainable=True)
    else:
        model = get_peft_model(model, METHODS[a.method]())
    for p in model.parameters():
        if p.requires_grad: p.data = p.data.float()
    res['trainable_params'] = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print('trainable params', res['trainable_params'], flush=True)
    args = TrainingArguments(output_dir=str(out / 'ckpt'), per_device_train_batch_size=1, gradient_accumulation_steps=a.grad_accum,
                             num_train_epochs=a.epochs, max_steps=a.max_steps, learning_rate=a.lr, lr_scheduler_type='cosine',
                             warmup_ratio=0.05, weight_decay=0.0, bf16=True, logging_steps=1, save_strategy='no',
                             report_to=[], dataloader_num_workers=4, seed=a.seed, remove_unused_columns=False,
                             group_by_length=False, max_grad_norm=1.0)
    t0 = time.time()
    trainer = Trainer(model=model, args=args, train_dataset=train, data_collator=lambda b: collate(b, pad))
    trainer.train()
    res['train_seconds'] = time.time() - t0
    res['train_log'] = [h for h in trainer.state.log_history if 'loss' in h]
    res['val_nll'], res['val_tokens'] = nll(model, val, pad)
    res['test_nll'], res['test_tokens'] = nll(model, test, pad)
    res['peak_mem_gb'] = torch.cuda.max_memory_allocated() / 1e9
    model.save_pretrained(out / 'adapter')
    (out / 'result.json').write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k != 'train_log'}), flush=True)


if __name__ == '__main__':
    main()
