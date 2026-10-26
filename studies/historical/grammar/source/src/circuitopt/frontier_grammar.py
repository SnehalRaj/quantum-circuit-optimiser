"""Typed native recipes with backend replacement and ordered arithmetic blocks."""
import copy
import hashlib
from importlib.resources import files
import json


EPSILON = 1e-3


def definition():
    return json.loads(files('circuitopt').joinpath('data/grammars/native-frontier-v2.json').read_text())


def validate_epsilon(value):
    if type(value) not in (int, float) or value != EPSILON:
        raise ValueError('This grammar requires epsilon exactly 0.001')
    return EPSILON


def recipe(backend='leapfrog'):
    spec=definition()
    if backend not in spec['backends']:raise ValueError('Unknown native backend')
    return {'backend':backend,'settings':{k:v['default'] for k,v in spec['backends'][backend]['settings'].items()},'program':None}


def _schedule(text, maximum_tick, maximum_value, uniform=False):
    if uniform and ':' not in text:
        if not text.isdigit() or int(text)>maximum_value:raise ValueError('Invalid uniform padding')
        return
    previous=-1
    for entry in text.split(','):
        parts=entry.split(':')
        if len(parts)!=2 or any(not p.isdigit() for p in parts):raise ValueError('Invalid schedule entry')
        tick,value=map(int,parts)
        if not previous<tick<=maximum_tick or value>maximum_value:raise ValueError('Schedule must be ordered, unique and bounded')
        previous=tick


def validate(value):
    spec=definition();out=copy.deepcopy(value)
    if not isinstance(out,dict) or set(out)!={'backend','settings','program'}:raise ValueError('Invalid native recipe fields')
    if out['backend'] not in spec['backends']:raise ValueError('Unknown native backend')
    settings=spec['backends'][out['backend']]['settings']
    if not isinstance(out['settings'],dict) or set(out['settings'])!=set(settings):raise ValueError('Every backend setting must be explicit')
    for key,rule in settings.items():
        number=out['settings'][key]
        if type(number) not in (str,int):raise ValueError('Settings must be explicit strings or integers')
        text=str(number);kind=rule['kind']
        if kind in ('int','bool'):
            lo,hi=(0,1) if kind=='bool' else (rule['min'],rule['max'])
            if not text.isdigit() or not lo<=int(text)<=hi:raise ValueError('Setting outside its domain: '+key)
            text=str(int(text))
        elif kind=='walk_schedule':_schedule(text,169,32,uniform=True)
        elif kind=='fold_profile':_schedule(text,699,32)
        elif kind=='fold_sites':
            if text:_schedule(text,8191,2)
        elif kind=='compare_pair':
            parts=text.split(',')
            if len(parts)!=2 or any(not p.isdigit() or int(p)>8 for p in parts):raise ValueError('Invalid comparison pair')
        else:raise ValueError('Unknown setting type')
        out['settings'][key]=text
    program=out['program']
    if program is not None:
        if spec['backends'][out['backend']]['program_context']!='leapfrog_replay':raise ValueError('This backend has no ordered replay-program boundary')
        rules=spec['program']
        if not isinstance(program,list) or not rules['minimum_length']<=len(program)<=rules['maximum_length']:raise ValueError('Invalid program length')
        for instruction in program:
            if not isinstance(instruction,dict) or set(instruction)!={'op','lowering'}:raise ValueError('Invalid arithmetic instruction')
            if instruction['op'] not in rules['operations'] or instruction['lowering'] not in rules['lowerings']:raise ValueError('Unknown arithmetic instruction')
    return out


def identity(value):
    return hashlib.sha256(json.dumps(validate(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def native_arguments(value):
    value=validate(value);arguments=['--backend',value['backend']]
    for name,setting in sorted(value['settings'].items()):arguments+=['--set',name+'='+setting]
    if value['backend']=='pingpong':arguments+=['--set','CP_PROFILE=1']
    if value['program'] is not None:
        text=','.join(i['op']+':'+i['lowering'] for i in value['program'])
        arguments+=['--set','AB_PROGRAM='+text]
    return arguments


def _program(value):
    return copy.deepcopy(value['program'] if value['program'] is not None else definition()['program']['default'])


def actions(value):
    value=validate(value);spec=definition();result=[]
    for backend in spec['backends']:
        if backend!=value['backend']:result.append({'rule':'backend','value':backend})
    descriptors=spec['backends'][value['backend']]['settings']
    for name,rule in descriptors.items():
        current=value['settings'][name]
        if rule['kind']=='bool':choices=[str(1-int(current))]
        elif rule['kind']=='int':
            steps=(-8,-4,-1,1,4,8) if 'CAP' in name or 'QUBITS' in name else (-4,-1,1,4)
            choices=[str(int(current)+step) for step in steps if rule['min']<=int(current)+step<=rule['max']]
        elif rule['kind']=='walk_schedule':choices=[str(n) for n in (2,3,4,5,6,8,12)]
        elif rule['kind']=='compare_pair':choices=[f'{a},{b}' for a,b in ((0,0),(1,1),(2,2),(3,3),(4,4),(6,6))]
        elif rule['kind']=='fold_profile':choices=['0:0','0:2','0:4','0:8']
        elif rule['kind']=='fold_sites':choices=['','746:0','746:1','746:2']
        else:choices=[]
        for choice in choices:
            if choice!=current:result.append({'rule':'setting','name':name,'value':choice})
    if spec['backends'][value['backend']]['program_context'] is None:return result
    p=_program(value);tokens=[{'op':op,'lowering':mode} for op in spec['program']['operations'] for mode in spec['program']['lowerings']]
    seeds=[spec['program']['default'],[{'op':'half_u','lowering':'direct'},{'op':'half','lowering':'direct'},{'op':'add','lowering':'direct'},{'op':'double_u','lowering':'direct'}]]
    for program in [None,*seeds]:
        if program!=value['program']:result.append({'rule':'program','value':copy.deepcopy(program)})
    for index,instruction in enumerate(p):
        result.append({'rule':'lowering','index':index})
        if len(p)>1:result.append({'rule':'delete','index':index})
        if index+1<len(p):result.append({'rule':'swap','index':index})
        for token in tokens:
            if token!=instruction:result.append({'rule':'replace','index':index,'instruction':token})
    if len(p)<spec['program']['maximum_length']:
        for index in range(len(p)+1):
            for token in tokens:result.append({'rule':'insert','index':index,'instruction':token})
    if len(p)+2<=spec['program']['maximum_length']:
        for op in spec['program']['operations']:result.append({'rule':'inverse_pair','index':len(p),'operation':op})
    return result


def apply(value,action):
    out=validate(value);rule=action['rule']
    if rule=='backend':return recipe(action['value'])
    if rule=='setting':out['settings'][action['name']]=action['value']
    elif rule=='program':out['program']=copy.deepcopy(action['value'])
    else:
        p=_program(out);index=action['index']
        if type(index) is not int or not 0<=index<=len(p):raise ValueError('Invalid instruction index')
        if rule=='insert':p.insert(index,copy.deepcopy(action['instruction']))
        elif rule=='delete':del p[index]
        elif rule=='replace':p[index]=copy.deepcopy(action['instruction'])
        elif rule=='swap':p[index],p[index+1]=p[index+1],p[index]
        elif rule=='lowering':p[index]['lowering']='power2' if p[index]['lowering']=='direct' else 'direct'
        elif rule=='inverse_pair':
            inverse={'swap':'swap','add':'sub','sub':'add','half':'double','double':'half','half_u':'double_u','double_u':'half_u'}
            op=action['operation'];p[index:index]=[{'op':op,'lowering':'direct'},{'op':inverse[op],'lowering':'direct'}]
        else:raise ValueError('Unknown native grammar action')
        out['program']=p
    return validate(out)
