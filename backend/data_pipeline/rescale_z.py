from pathlib import Path
import pandas as pd
from dotenv import load_dotenv, find_dotenv
import os

load_dotenv(find_dotenv())
root = Path(os.getenv("ROOT_DIR", Path(__file__).parent))
data = f"{root}data"
nodes = f"{data}network_nodes.csv"
out = f"{data}network_nodes_rescaled.csv" 

df = pd.read_csv(nodes)

z_min, z_max = df["z"].min(), df["z"].max()
df["z"] = (df["z"] - z_min) * (800 / (z_max - z_min))

df.to_csv(out, index=False)
print("finished")