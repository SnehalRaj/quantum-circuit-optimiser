//! Extract local windows from an existing circuit without candidate code.
use quantum_ecc::circuit::{Op, OperationType as K, QubitId, NO_BIT, NO_QUBIT, NO_REG};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;

fn name(k: K) -> Option<&'static str> {
    Some(match k { K::Neg=>"NEG", K::X=>"X", K::Z=>"Z", K::CX=>"CX", K::CZ=>"CZ",
        K::Swap=>"SWAP", K::CCX=>"CCX", K::CCZ=>"CCZ", _=>return None })
}
fn local(ops: &[Op], max_wires: usize) -> Option<(Vec<(String, Vec<usize>)>, Vec<QubitId>)> {
    let mut wires = Vec::new(); let mut gates = Vec::new();
    for op in ops {
        let kind=name(op.kind)?;
        if op.c_condition!=NO_BIT || op.c_target!=NO_BIT || op.r_target!=NO_REG {return None;}
        let mut ids=Vec::new();
        for q in [op.q_target,op.q_control1,op.q_control2] {
            if q==NO_QUBIT {continue;}
            if !wires.contains(&q) {wires.push(q);}
            if wires.len()>max_wires {return None;}
            ids.push(wires.iter().position(|&w|w==q).unwrap());
        }
        gates.push((kind.to_string(),ids));
    }
    Some((gates,wires))
}
fn as_json(gates:&[(String,Vec<usize>)])->Value {
    Value::Array(gates.iter().map(|(kind,qs)| {
        let mut g=vec![json!(kind)];g.extend(qs.iter().map(|q|json!(q)));Value::Array(g)
    }).collect())
}
fn nonlinear(ops:&[Op])->usize {ops.iter().filter(|o|matches!(o.kind,K::CCX|K::CCZ)).count()}
fn run()->Result<(),String> {
    let args:Vec<String>=std::env::args().skip(1).collect();
    if args==["--help"] {
        println!("rewrite-mine --ops PATH --width 16|256 --out PATH [--max-wires 4] [--limit 10000]");return Ok(());
    }
    let mut opt=BTreeMap::new();let mut it=args.iter();
    while let Some(k)=it.next() {
        if !["--ops","--width","--out","--max-wires","--limit"].contains(&k.as_str()){return Err(format!("unknown {k}"));}
        let v=it.next().ok_or("missing value")?;
        if opt.insert(k.as_str(),v.as_str()).is_some(){return Err("duplicate option".into());}
    }
    let get=|k|opt.get(k).copied().ok_or_else(||format!("missing {k}"));
    let width=get("--width")?.parse::<usize>().map_err(|_|"invalid width")?;
    let max_wires=opt.get("--max-wires").unwrap_or(&"4").parse::<usize>().map_err(|_|"invalid wires")?;
    let limit=opt.get("--limit").unwrap_or(&"10000").parse::<usize>().map_err(|_|"invalid limit")?;
    if !(3..=6).contains(&max_wires)||limit==0{return Err("invalid bounds".into());}
    let output=Path::new(get("--out")?);if output.exists(){return Err("output exists".into());}
    let input=Path::new(get("--ops")?);
    let bytes=std::fs::read(input).map_err(|e|e.to_string())?;
    let source_hash=lco_native::sha256(&bytes);
    let ops=study_check::parse_ops_with_width(&bytes,width)?;drop(bytes);
    let mut counts:HashMap<Vec<(String,Vec<usize>)>,(usize,usize)>=HashMap::new();
    for end in 3..=ops.len() {
        if end%5_000_000==0{eprintln!("mined {end}/{} operations",ops.len());}
        for length in 3..=7.min(end) {
            let window=&ops[end-length..end];
            if nonlinear(window)<2 {continue;}
            if let Some((gates,wires))=local(window,max_wires) {
                if wires.len()>=3 {let value=counts.entry(gates).or_insert((0,wires.len()));value.0+=1;}
            }
        }
    }
    let mut tasks:Vec<Value>=counts.into_iter().map(|(g,(count,n))| {
        let cost=g.iter().filter(|(k,_)|k=="CCX"||k=="CCZ").count();
        json!({"wires":n,"before":as_json(&g),"occurrences":count,"rank_weight":count*cost})
    }).collect();
    tasks.sort_by(|a,b|b["rank_weight"].as_u64().cmp(&a["rank_weight"].as_u64())
        .then(b["occurrences"].as_u64().cmp(&a["occurrences"].as_u64()))
        .then(a["before"].as_array().unwrap().len().cmp(&b["before"].as_array().unwrap().len()))
        .then(a["before"].to_string().cmp(&b["before"].to_string())));
    let total=tasks.len();tasks.truncate(limit);
    let value=json!({"schema":"lco-rewrite-corpus-v1","input_ops_sha256":source_hash,"field_width":width,
        "operations":ops.len(),"max_wires":max_wires,"unique_patterns":total,"tasks":tasks});
    study_check::atomic_json(output,&value).map_err(|e|e.to_string())?;
    println!("{}",json!({"operations":ops.len(),"unique_patterns":total,"out":output}));Ok(())
}
fn main(){if let Err(e)=run(){eprintln!("rewrite-mine: {e}");std::process::exit(2);}}
