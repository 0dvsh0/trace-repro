from falkordb import FalkorDB

db = FalkorDB(host='localhost', port=6379).select_graph('demo')
res = db.query("MATCH (s:Span) RETURN properties(s) LIMIT 5")
for r in res.result_set:
    print(r)
