"""Run a division evaluation in a process with an outer wall-clock timeout."""
import json
import sys
import resource
import platform
from .division import export_circuit, evaluate_saved

request=json.load(sys.stdin)
export_circuit(request['recipe'],request['circuit'])
result=evaluate_saved(request['circuit'],request['pairs'],request['label'],request['inputs'])
usage=resource.getrusage(resource.RUSAGE_SELF)
result['cpu_seconds']=usage.ru_utime+usage.ru_stime
result['peak_rss_bytes']=usage.ru_maxrss*(1 if platform.system()=='Darwin' else 1024)
print(json.dumps(result))
