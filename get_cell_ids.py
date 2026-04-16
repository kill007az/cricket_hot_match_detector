import json
nb = json.load(open('notebooks/08_hotness_formula_tuning.ipynb'))
for i, c in enumerate(nb['cells']):
    print(i, c.get('id','no-id'), c['cell_type'], repr(c['source'][:80]))
