"""Shared native actions and two parent-selection policies."""
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict
import copy
import json
from pathlib import Path
import random

from . import swarm_native


def action_menu(recipe, profile, eligible, seed, limit=96):
    """Keep a bounded, reproducible sample from every available action family."""
    if type(limit) is not int or limit<1:
        raise ValueError('The action menu needs a positive limit')
    actions=swarm_native.legal_actions(recipe,profile,eligible)
    groups=defaultdict(list)
    for action in actions:groups[action['rule']].append(action)
    rng=random.Random(seed)
    for name in sorted(groups):rng.shuffle(groups[name])
    result=[]
    while len(result)<limit and any(groups.values()):
        for name in sorted(groups):
            if groups[name] and len(result)<limit:result.append(groups[name].pop())
    return result


def decode_action(reply, menu):
    if not isinstance(reply,dict) or set(reply)!={'action_index','reason'}:
        raise ValueError('Return only the action index and reason')
    index=reply['action_index']
    if type(index) is not int or not 0<=index<len(menu) or not isinstance(reply['reason'],str):
        raise ValueError('The reply must select one listed native action')
    return copy.deepcopy(menu[index])


class PopulationPolicy:
    """Use the current native policy or the pinned OpenEvolve database."""
    def __init__(self, arm, seed, output):
        if arm not in ('current','openevolve'):raise ValueError('Unknown pilot arm')
        self.arm=arm;self.seed=seed;self.output=Path(output)
        self.output.mkdir(parents=True,exist_ok=True)
        self.archive={};self.rng=random.Random(seed)
        self.random_state=random.Random(seed).getstate()
        self.database=None;self.reference=None;self.last_parent=None;self.last_slot=None
        self.additions=0
        if arm=='openevolve':
            from openevolve.config import DatabaseConfig
            from openevolve.database import ProgramDatabase
            config=DatabaseConfig(db_path=str(self.output/'database'),in_memory=False,
                population_size=128,archive_size=64,num_islands=2,
                feature_dimensions=['Q','T'],feature_bins=8,random_seed=seed,
                migration_interval=8,migration_rate=.1,cleanup_old_artifacts=False,
                embedding_model=None,novelty_llm=None,max_snapshot_artifacts=None)
            with self.random_context():self.database=ProgramDatabase(config)

    @contextmanager
    def random_context(self):
        previous=random.getstate();random.setstate(self.random_state)
        try:yield
        finally:self.random_state=random.getstate();random.setstate(previous)

    def add(self, row):
        if row['screen']['failed_inputs'] or row['id'] in self.archive:return False
        row=copy.deepcopy(row);initial=not self.archive
        self.archive[row['id']]=row
        if self.reference is None:self.reference=row['screen']['S']
        if self.database is not None:
            from openevolve.database import Program
            destinations=range(2) if initial else [self.last_slot%2]
            for island in destinations:
                pid=f"{self.additions}:{island}:{row['id']}"
                program=Program(id=pid,code=json.dumps(row['recipe'],sort_keys=True,indent=2),
                    language='json',parent_id=self.last_parent,generation=row['depth'],
                    metrics={'combined_score':self.reference/row['screen']['S'],
                             'Q':float(row['screen']['Q']),'T':float(row['screen']['T'])},
                    metadata={'candidate_id':row['id'],'native_sha256':row['artifact']['sha256']})
                with self.random_context():
                    self.database.add(program,iteration=self.additions,target_island=island)
            self.additions+=1
            self.checkpoint()
        return True

    def choose(self, slot):
        if not self.archive:raise ValueError('A parent pool needs a passing baseline')
        self.last_slot=slot
        if self.database is not None:
            with self.random_context():
                island=slot%2
                self.database.set_current_island(island)
                parent,inspirations=self.database.sample_from_island(island,num_inspirations=2)
                self.database.increment_island_generation(island)
                if self.database.should_migrate():self.database.migrate_programs()
            self.last_parent=parent.id
            row=self.archive[parent.metadata['candidate_id']]
            examples=[self.archive[p.metadata['candidate_id']] for p in inspirations]
        else:
            row=swarm_native.pick_parent(self.archive,self.rng,128,slot)
            candidates=[r for r in self.archive.values() if r['id']!=row['id']]
            examples=sorted(candidates,key=lambda r:(r['screen']['S'],r['id']))[:2]
        unique=[];seen={row['id']}
        for example in examples:
            if example['id'] not in seen:unique.append(example);seen.add(example['id'])
        return copy.deepcopy(row),copy.deepcopy(unique[:2])

    def checkpoint(self):
        if self.database is not None:
            with self.random_context():self.database.save(iteration=self.additions)

    def configuration(self):
        return asdict(self.database.config) if self.database is not None else {
            'implementation':'circuitopt.swarm_native.pick_parent','maximum_depth':128,
            'inspirations':'Two cheapest distinct valid recipes excluding the parent'}
