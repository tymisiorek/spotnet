import os
import math
import csv
import json
import random
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
import igraph as ig



load_dotenv(find_dotenv())
root = os.getenv("ROOT_DIR", f"{Path(__file__).parent.parent}")
data = Path(f"{root}/data")
nodes_csv = data / "network_nodes.csv"
edges_csv = data / "network_edges.csv"
output_json = data / "graph.json"



