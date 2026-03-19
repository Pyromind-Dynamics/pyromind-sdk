# PyroMind SDK Skill Scripts

These scripts are customer-facing helpers for common local tasks.

## Requirements

- Python 3.8+
- `pyromind-sdk` installed

```bash
pip install pyromind-sdk
```

## Scripts

### 1) Convert workflow

```bash
python skill/scripts/convert_workflow.py INPUT.json OUTPUT.json
python skill/scripts/convert_workflow.py --to-standard INPUT.lite.json OUTPUT.json
```

- Default direction: `standard -> lite`
- `--to-standard`: `lite -> standard`

### 2) Validate workflow

```bash
python skill/scripts/validate_workflow.py INPUT.json
python skill/scripts/validate_workflow.py --format standard INPUT.json
python skill/scripts/validate_workflow.py --format lite INPUT.lite.json
```

- `--format auto` (default): detect by `nodes` structure

### 3) Round-trip check

```bash
python skill/scripts/roundtrip_check.py INPUT.json
python skill/scripts/roundtrip_check.py INPUT.json --output REGENERATED.json
```

- Runs `standard -> lite -> standard`
- Prints node/link count and `last_node_id`/`last_link_id`

### 4) Inspect YAML node

```bash
python skill/scripts/inspect_yaml_node.py NODE.yaml
python skill/scripts/inspect_yaml_node.py NODE.yaml --json
```

- Loads YAML node classes and prints key metadata

### 5) API examples (Jupyter / Inference / Sandbox)

```bash
# Uses PYROMIND_API_KEY and optional PYROMIND_BASE_URL from env
python skill/scripts/api_examples.py --mode jupyter --name demo-jupyter --cpu 2 --memory 8

python skill/scripts/api_examples.py --mode inference \
  --name demo-inference \
  --model-path /workspace/models/qwen \
  --framework vllm \
  --cpu 4 --memory 16 --gpu 1 --gpu-card L40S

python skill/scripts/api_examples.py --mode sandbox --name demo-sandbox --cpu 2 --memory 4
```

- `--api-key` and `--base-url` can override environment variables
- `--mode` is required: `jupyter`, `inference`, or `sandbox`

## Notes

- These scripts focus on workflow/YAML utilities.
- For remote platform operations (Jupyter/Inference/Sandbox), use the API client in `skill/SKILL.md`.
