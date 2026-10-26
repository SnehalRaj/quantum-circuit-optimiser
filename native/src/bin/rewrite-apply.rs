//! Apply proposed local rules after fixed-simulator certificates.
use quantum_ecc::circuit::{Op, OperationType as K, QubitId, NO_BIT, NO_QUBIT, NO_REG};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::Path;
type Gate = (K, Vec<usize>);
struct Rule { before: Vec<Gate>, after: Vec<Gate>, wires: usize }
fn gate_name(k:K)->Option<&'static str> {Some(match k {K::Neg=>"NEG",K::X=>"X",K::Z=>"Z",K::CX=>"CX",K::CZ=>"CZ",K::Swap=>"SWAP",K::CCX=>"CCX",K::CCZ=>"CCZ",_=>return None})}
fn cost(gates:&[Gate])->usize {gates.iter().filter(|g|matches!(g.0,K::CCX|K::CCZ)).count()}
fn lift(g:&Gate,w:&[QubitId])->Op {
 let mut o=Op::empty();o.kind=g.0;
 if !g.1.is_empty(){o.q_target=w[g.1[0]];}
 if g.1.len()>1{o.q_control1=w[g.1[1]];}
 if g.1.len()>2{o.q_control2=w[g.1[2]];}o
}
fn parse_gates(value:&Value,n:usize)->Result<Vec<Gate>,String> {
 let array=value.as_array().ok_or("program must be a list")?;
 if array.len()>7{return Err("at most seven gates".into());}
 array.iter().map(|g| {
  let items=g.as_array().ok_or("gate must be a list")?;
  let name=items.first().and_then(Value::as_str).ok_or("missing gate name")?;
  let kind=K::from_name(name).ok_or("unknown gate")?;
  if gate_name(kind).is_none(){return Err("nonunitary or classical gate".into());}
  let arity=match kind{K::Neg=>0,K::X|K::Z=>1,K::CX|K::CZ|K::Swap=>2,_=>3};
  if items.len()!=arity+1{return Err("wrong gate arity".into());}
  let mut ids=Vec::new();for x in &items[1..] {
   let q=x.as_u64().ok_or("wire must be an unsigned integer")? as usize;
   if q>=n||ids.contains(&q){return Err("wire bound or alias error".into());}ids.push(q);
  }
  Ok((kind,ids))
 }).collect()
}
fn signature(gates:&[Gate],n:usize)->Vec<u8> {
 let mut random=study_check::stream(b"lco-learned-rule-certificate",&[]);
 let mut sim=quantum_ecc::sim::Simulator::new(n,0,&mut random);
 for lane in 0..64 {for q in 0..n{if (lane>>q)&1!=0{sim.qubits[q]|=1u64<<lane;}}}
 let w:Vec<_>=(0..n).map(|q|QubitId(q as u64)).collect();
 let ops:Vec<_>=gates.iter().map(|g|lift(g,&w)).collect();
 for o in &ops{o.validate();}sim.apply_iter(ops.iter());
 (0..1usize<<n).map(|lane| {
  let mut x=0u8;for q in 0..n{x|=(((sim.qubits[q]>>lane)&1) as u8)<<(q+1);}
  x|((sim.phase>>lane)&1) as u8
 }).collect()
}
fn parse_rules(value:&Value)->Result<Vec<Rule>,String> {
 value.as_array().ok_or("rules must be a list")?.iter().map(|r| {
  let n=r["wires"].as_u64().ok_or("missing wires")? as usize;
  if !(1..=6).contains(&n){return Err("one to six wires required".into());}
  let before=parse_gates(&r["before"],n)?;let after=parse_gates(&r["after"],n)?;
  if cost(&before)<=cost(&after)||signature(&before,n)!=signature(&after,n){return Err("rule fails exact improving certificate".into());}
  let mut seen=Vec::new();for (_,ids) in &before {for &q in ids {if !seen.contains(&q){seen.push(q);}}}
  if seen!=(0..n).collect::<Vec<_>>(){return Err("use canonical first-occurrence wire names".into());}
  Ok(Rule{before,after,wires:n})
 }).collect()
}
fn matching(ops:&[Op],r:&Rule)->Option<Vec<QubitId>> {
 if ops.len()!=r.before.len(){return None;}
 let mut wires=Vec::with_capacity(r.wires);
 for (op,gate) in ops.iter().zip(&r.before) {
  if op.kind!=gate.0||op.c_condition!=NO_BIT||op.c_target!=NO_BIT||op.r_target!=NO_REG{return None;}
  let qs:Vec<_>=[op.q_target,op.q_control1,op.q_control2].into_iter().filter(|q|*q!=NO_QUBIT).collect();
  if qs.len()!=gate.1.len(){return None;}
  for (q,id) in qs.into_iter().zip(&gate.1){
   if let Some(old)=wires.iter().position(|w|*w==q){if old!=*id{return None;}}
   else {if wires.len()!=*id{return None;}wires.push(q);}
  }
 }
 Some(wires)
}
fn apply(ops:Vec<Op>,rules:&[Rule])->(Vec<Op>,Vec<usize>){
 let mut out:Vec<Op>=Vec::with_capacity(ops.len());let mut hits=vec![0;rules.len()];
 for op in ops {out.push(op);loop {
  let mut changed=false;
  for (i,r) in rules.iter().enumerate(){let len=r.before.len();if out.len()<len{continue;}
   if let Some(wires)=matching(&out[out.len()-len..],r){
    out.truncate(out.len()-len);out.extend(r.after.iter().map(|g|lift(g,&wires)));hits[i]+=1;changed=true;break;
   }
  }
  if !changed{break;}
 }}(out,hits)
}
fn run()->Result<(),String>{
 let args:Vec<_>=std::env::args().skip(1).collect();
 if args==["--help"]{println!("rewrite-apply --width 16|256 --ops PATH --rules JSON --out PATH");return Ok(());}
 let mut opt=BTreeMap::new();let mut it=args.iter();while let Some(k)=it.next(){
  if !["--width","--ops","--rules","--out"].contains(&k.as_str()){return Err(format!("unknown {k}"));}
  let v=it.next().ok_or("missing value")?;if opt.insert(k.as_str(),v.as_str()).is_some(){return Err("duplicate option".into());}
 }
 let get=|k|opt.get(k).copied().ok_or_else(||format!("missing {k}"));
 let input=Path::new(get("--ops")?);let out=Path::new(get("--out")?);let receipt=out.with_extension("json");
 if out.exists()||receipt.exists(){return Err("output exists".into());}
 let rules_path=Path::new(get("--rules")?);
 let rule_bytes=std::fs::read(rules_path).map_err(|e|e.to_string())?;
 let value:Value=serde_json::from_slice(&rule_bytes).map_err(|e|e.to_string())?;
 let mut rules=parse_rules(&value)?;rules.sort_by_key(|r|std::cmp::Reverse(r.before.len()));
 let width=get("--width")?.parse::<usize>().map_err(|_|"invalid width")?;
 let bytes=std::fs::read(input).map_err(|e|e.to_string())?;let input_sha=lco_native::sha256(&bytes);
 let ops=study_check::parse_ops_with_width(&bytes,width)?;drop(bytes);let old_len=ops.len();
 let (ops,hits)=apply(ops,&rules);lco_native::write_ops(&ops,out)?;
 let saved:usize=rules.iter().zip(&hits).map(|(r,h)|h*(cost(&r.before)-cost(&r.after))).sum();
 let record=json!({"schema":"lco-proved-rules-v1","field_width":width,"input_ops_sha256":input_sha,
  "ops_sha256":lco_native::file_sha256(out)?,"rules_sha256":lco_native::sha256(&rule_bytes),
  "input_ops":old_len,"output_ops":ops.len(),"rewrites":hits.iter().sum::<usize>(),"static_toffoli_saved":saved,
  "certificate":"fixed simulator, all basis states and signs, no extra wires, unitary windows with no classical fields",
  "rule_count":rules.len(),"scope":"exact equivalence to parent; parent correctness requires a separate audit"});
 study_check::atomic_json(&receipt,&record).map_err(|e|e.to_string())?;println!("{record}");Ok(())
}
fn main(){if let Err(e)=run(){eprintln!("rewrite-apply: {e}");std::process::exit(2);}}
#[cfg(test)]mod tests{
 use super::*;
 #[test]fn proves_factoring_and_rejects_phase(){
 let r=json!([{"wires":3,"before":[["CCX",0,1,2],["X",1],["CCX",0,1,2]],"after":[["CX",0,2],["X",1]]}]);
 assert!(parse_rules(&r).is_ok());
 assert!(parse_rules(&json!([{"wires":3,"before":[["CCZ",0,1,2]],"after":[]}])).is_err());
 }
 #[test]fn preserves_measurement_barriers(){
 let rules=parse_rules(&json!([{"wires":3,"before":[["CCX",0,1,2],["X",1],["CCX",0,1,2]],"after":[["CX",0,2],["X",1]]}])).unwrap();
 let w=vec![QubitId(4),QubitId(1),QubitId(7)];let mut ops:Vec<_>=rules[0].before.iter().map(|g|lift(g,&w)).collect();
 let (out,hits)=apply(ops.clone(),&rules);assert_eq!(hits,vec![1]);assert_eq!(out.len(),2);
 let mut barrier=Op::empty();barrier.kind=K::Hmr;ops.insert(1,barrier);
 let (out,hits)=apply(ops.clone(),&rules);assert_eq!(out,ops);assert_eq!(hits,vec![0]);
 }
}
