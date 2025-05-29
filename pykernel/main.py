
executable_path = "executables"
import os
import sys
from fastapi import FastAPI
import subprocess


if not os.path.exists(executable_path):
    print(f"Error: The path '{executable_path}' does not exist.")
    sys.exit(1)

if not os.path.isdir(executable_path):
    print(f"Error: The path '{executable_path}' is not a directory.")
    sys.exit(1)

if not os.access(executable_path, os.R_OK):
    print(f"Error: The path '{executable_path}' is not readable.")
    sys.exit(1)

# Read contents of the directory
try:
    files = os.listdir(executable_path)
except OSError as e:
    print(f"Error: Unable to list files in '{executable_path}': {e}")
    sys.exit(1)

for file in files:
    if file.endswith(".cmd"):
        print(f"Found executable: {file}")

app = FastAPI()

@app.post("/execute")
def execute_command(command: str):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return {"stdout": result.stdout, "stderr": result.stderr}
    except Exception as e:
        return {"error": str(e)}

@app.get("/")
def read_root():
    return {"Hello": "World"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)