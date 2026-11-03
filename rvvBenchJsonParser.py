#!/usr/bin/python3

import json
import re
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib
import pandas

jsonFile="./bench/emu.result/memcpy.json"
jsonFile="/nfs/home/huxuan/repos/XiangShanDebug/emu.rvv-bench.result/rvvBench.json"
SEABORN_DATA_HOME="/nfs/home/huxuan/repos/seaborn-data"

def add_eol_commas(json_string):
  json_string = re.sub(r'(:\s+[\w"]+)(\s+[\w"]+:)', r'\1,\2', json_string)
  return json_string

def remove_tail_commas(json_string):
  json_string = re.sub(r',\s*}', '}', json_string)
  json_string = re.sub(r',\s*]', ']', json_string)
  json_string = re.sub(r',\s*$', r'', json_string)
  return json_string

def add_double_quotes_to_keys(json_string):
  json_string = re.sub(r'(\w+)\s*:', r'"\1":', json_string)
  return json_string

with open(jsonFile, 'r') as file:
  string = file.read()
  # print(string)
  string = add_double_quotes_to_keys(string)
  string = add_eol_commas(string)
  string = remove_tail_commas(string)
  data:dict = json.loads(string)

class RVVBenchResult:

  def __init__(self, testName, js:dict):
    self.testName = testName
    self.caseNames = js["labels"][1:] 
    self.benchSizes = js["data"][0]
    # New benchmark output keeps raw cycle repeats in a separate field.
    # Fall back to the legacy layout for previously generated JSON files.
    data = js.get("repeats", js["data"][1:])
    self.repeatTime = len(data[0][0])
    self.results = {}
    for i in range(len(self.caseNames)):
      name = self.caseNames[i]
      result = {}
      for j in range(0, len(self.benchSizes)):
        sz = self.benchSizes[j]
        result[sz] = data[i][j]
      self.results[name] = result

  def toDataFrame(self):
    lst = []
    for name, result in self.results.items():
      for sz, datas in result.items():
        if datas:
          mean_cycles = np.mean(datas)
          if mean_cycles != 0:
            lst.append([name, sz, sz / mean_cycles])
    df = pandas.DataFrame(lst, columns=['case', 'size', 'Bpc'])
    return df
      
  

benchResults: dict[str,RVVBenchResult] = {}

for key,value in data.items():
  benchResults[key] = RVVBenchResult(key, value)



# dfs = {}
# for key, res in benchResults["memcpy"].results["scalar"].items():
#   df = pandas.DataFrame(res)
#   dfs[key] = df

# print(dfs)

# print(benchResults["memcpy"].toDataFrame())
matplotlib.use("GTK3Agg")

for name, res in benchResults.items():
  df = res.toDataFrame()
  print(df)
  fig, ax = plt.subplots(figsize=(20, 12))
  sns.lineplot(df, x = "size", y = "Bpc", hue = "case", ax=ax)
  ax.set_xscale("log")
  fig.savefig(f"{name}.png")
