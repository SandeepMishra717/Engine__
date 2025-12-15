# tests/test_engine.py
import json
from app.engine_core import load_fields_config, load_rules, PathResolver, RuleDispatcher, build_context_from_docs

def run_test():
    ctx = json.load(open('tests/dummy_data.json', 'r', encoding='utf-8'))
    resolver = PathResolver(load_fields_config())
    rules = load_rules()
    dispatcher = RuleDispatcher(resolver=resolver, rules=rules)
    results = dispatcher.evaluate(ctx)
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    run_test()
