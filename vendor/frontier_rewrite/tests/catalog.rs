#[path="../catalog.rs"] mod catalog;
use quantum_ecc::circuit::{Op,OperationType as K,QubitId};
fn op(k:K,t:u64,a:u64,b:u64)->Op {let mut o=Op::empty();o.kind=k;o.q_target=QubitId(t);o.q_control1=QubitId(a);if b!=u64::MAX{o.q_control2=QubitId(b);}o}
#[test]
fn removes_nonlinearity_from_control_conjugation(){
 let u=op(K::CCX,2,0,1);let v=op(K::CX,1,0,u64::MAX);
 let before=vec![u,v,u];let out=catalog::optimize(before.clone());
 assert_eq!(out.iter().filter(|o|matches!(o.kind,K::CCX|K::CCZ)).count(),0);
 assert!(catalog::equivalent(&before,&out));
}
#[test]
fn preserves_phase_and_controlled_copy_conjugations(){
 let u=op(K::CCX,2,0,1);let v=op(K::CX,3,2,u64::MAX);
 let before=vec![u,v,u];let out=catalog::optimize(before.clone());
 assert_eq!(out.iter().filter(|o|matches!(o.kind,K::CCX|K::CCZ)).count(),1);
 assert!(catalog::equivalent(&before,&out));
 let mut z=Op::empty();z.kind=K::Z;z.q_target=QubitId(2);
 let before=vec![u,z,u];let out=catalog::optimize(before.clone());
 assert_eq!(out.iter().filter(|o|matches!(o.kind,K::CCX|K::CCZ)).count(),0);
 assert!(catalog::equivalent(&before,&out));
}
#[test]
fn keeps_measurement_barriers(){
 let u=op(K::CCX,2,0,1);let mut m=Op::empty();m.kind=K::R;m.q_target=QubitId(3);
 let before=vec![u,m,u];assert_eq!(catalog::optimize(before.clone()),before);
}

#[test]
fn fixed_simulator_checks_noncontiguous_roles_and_outer_masks(){
 let roles=[0,2,4,5];
 let u=op(K::CCX,roles[2],roles[0],roles[1]);
 let v=op(K::CX,roles[1],roles[0],u64::MAX);
 let mut p0=Op::empty();p0.kind=K::PushCondition;p0.c_condition=quantum_ecc::circuit::BitId(0);
 let mut p1=p0;p1.c_condition=quantum_ecc::circuit::BitId(1);
 let mut pop=Op::empty();pop.kind=K::PopCondition;
 let before=vec![p0,p1,u,v,u,pop,pop];let after=catalog::optimize(before.clone());
 let simulate=|ops:&[Op],x:u64| {
  let mut rng=study_check::stream(b"catalog-role-lift",&[]);
  let mut s=quantum_ecc::sim::Simulator::new(6,2,&mut rng);
  s.bits[0]=0xaaaaaaaaaaaaaaaa;s.bits[1]=0xcccccccccccccccc;
  for q in 0..6 {s.qubits[q]=if (x>>q)&1!=0{u64::MAX}else{0};}
  s.apply_iter(ops.iter());(s.qubits,s.bits,s.phase,s.stats.toffoli_gates)
 };
 for x in 0..64 {
  let a=simulate(&before,x);let b=simulate(&after,x);
  assert_eq!((&a.0,&a.1,a.2),(&b.0,&b.1,b.2));assert!(b.3<a.3);
 }
 let mut conditioned=u;conditioned.c_condition=quantum_ecc::circuit::BitId(0);
 let before=vec![u,v,conditioned];assert_eq!(catalog::optimize(before.clone()),before);
}
