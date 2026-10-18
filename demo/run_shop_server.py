import os
import sys
import subprocess

os.environ["TRACE_REPRO_ARMED"] = "1"
os.environ["TRACE_REPRO_GRAPH"] = "falkordb://localhost:6379/demo"
os.environ["SHOP_DEBUG"] = "1"

python_exe = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".venv-shop", "Scripts", "python.exe"))
manage_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shop", "manage.py"))

print(f"Starting shop server with ARMED=1 and GRAPH={os.environ['TRACE_REPRO_GRAPH']}")
subprocess.run([python_exe, manage_py, "runserver", "127.0.0.1:8000", "--noreload"])
