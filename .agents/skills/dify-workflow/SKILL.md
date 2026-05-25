---
name: dify-workflow
description: Use when generating or reviewing Dify 1.14.2 workflow/chatflow DSL files, especially workflows with Code nodes, imported YAML/JSON apps, node schemas, edges, layout, or Dify DSL 0.6.0 compatibility.
---

# Dify Workflow DSL Generator

## Overview

This local copy is patched for Dify 1.14.2 and App DSL 0.6.0. It generates Dify workflow DSL files that can be imported into a Dify instance. Given a natural language description of a desired workflow, it produces a complete YAML (default) or JSON file containing all nodes, edges, layout positions, and configuration.

The skill triggers when the user asks to:
- Create, generate, or build a Dify workflow
- Convert a process description into a Dify DSL
- Scaffold a chatflow or workflow application

Output format is YAML by default (`.dify.yml`), with JSON (`.dify.json`) available on request.

## Dify 1.14.2 Guardrails

Apply these rules before emitting or reviewing a DSL:

- Treat this skill as targeting Dify 1.14.2, whose current App DSL version is `0.6.0`.
- For Code nodes, load `references/nodes/code.md` and follow its Dify 1.14.2 restrictions exactly.
- Code nodes support only `code_language: python3` and `code_language: javascript`; never use `json` as a Code node language.
- Code node `outputs` may use only `string`, `number`, `object`, `boolean`, `array[string]`, `array[number]`, `array[object]`, or `array[boolean]`.
- Code node `outputs` must exactly match the keys returned by `main()`: no missing keys and no extra keys.
- When a workflow needs newer or less-covered nodes such as `human-input`, `agent`, `loop`, `list-operator`, `datasource`, or complex MCP/tool behavior, prefer starting from a DSL exported by the target Dify 1.14.2 instance instead of inventing the schema from memory.

## Smart Interaction Logic

Before generating, assess whether the user's description is sufficient:

**Proceed directly** if the description includes:
- Clear input/output expectations
- Processing logic (what the workflow should do)
- Enough detail to select appropriate nodes

**Ask clarifying questions** (max 3 rounds) if unclear:
1. "What inputs will the workflow receive, and what outputs should it produce?"
2. "What processing steps are needed? (e.g., LLM call, knowledge retrieval, API call, conditional logic)"
3. "Any specific models, tools, or knowledge bases to use?"

Once requirements are clear, proceed to generation.

## Node Router Table

| Node | Type Key | Purpose | Key Params | Schema Path |
|------|----------|---------|------------|-------------|
| Start | `start` | Entry point; defines input variables | `variables` | `references/nodes/start.md` |
| End | `end` | Terminal node for Workflow mode; declares outputs | `outputs` | `references/nodes/end.md` |
| Answer | `answer` | Streams response in Chatflow mode | `answer`, `variables` | `references/nodes/answer.md` |
| LLM | `llm` | Invokes a large language model | `model`, `prompt_template`, `context`, `vision` | `references/nodes/llm.md` |
| Knowledge Retrieval | `knowledge-retrieval` | Searches knowledge bases for relevant chunks | `query_variable_selector`, `dataset_ids`, `retrieval_mode` | `references/nodes/knowledge-retrieval.md` |
| Code | `code` | Executes Python3 or JavaScript code | `code_language`, `code`, `variables`, `outputs` | `references/nodes/code.md` |
| HTTP Request | `http-request` | Makes HTTP API calls | `method`, `url`, `headers`, `body`, `authorization` | `references/nodes/http-request.md` |
| If/Else | `if-else` | Conditional branching (IF/ELIF/ELSE) | `cases` | `references/nodes/if-else.md` |
| Variable Aggregator | `variable-aggregator` | Merges variables from multiple branches | `output_type`, `variables` | `references/nodes/variable-aggregator.md` |
| Iteration | `iteration` | Loops over array, runs sub-graph per element | `iterator_selector`, `iterator_input_type`, `output_selector`, `start_node_id` | `references/nodes/iteration.md` |
| Document Extractor | `document-extractor` | Extracts text from uploaded files (PDF/DOCX/etc.) | `variable_selector`, `is_array_file` | `references/nodes/document-extractor.md` |
| Template Transform | `template-transform` | Renders Jinja2 templates with variables | `template`, `variables` | `references/nodes/template-transform.md` |
| Question Classifier | `question-classifier` | Routes by classifying input into categories via LLM | `query_variable_selector`, `model`, `classes` | `references/nodes/question-classifier.md` |
| Parameter Extractor | `parameter-extractor` | Extracts structured params from text via LLM | `query`, `model`, `parameters`, `reasoning_mode` | `references/nodes/parameter-extractor.md` |
| Tool | `tool` | Invokes external tools (built-in, API, MCP) | `provider_id`, `provider_type`, `tool_name`, `tool_parameters` | `references/nodes/tool.md` |

## Generation Flow

Follow these steps to produce a valid DSL file:

1. **Parse requirement** -- Identify the app mode (`workflow` or `advanced-chat`), needed nodes, and data flow.
   - Use `workflow` mode with Start/End nodes for batch processing tasks.
   - Use `advanced-chat` mode with Start/Answer nodes for conversational chatbots.

2. **Select nodes** from the router table above. Load the corresponding schema file for each selected node to get the full field specification.

3. **Check template match** -- If the requirement closely matches a known pattern, start from a template (see Template Matching below). Adapt fields as needed.

4. **Assemble from schemas** -- If no template matches, build nodes individually. For each node:
   - Generate a unique ID (13-digit timestamp string, e.g., `"1711536487001"`).
   - Fill required fields per the node schema.
   - Use `{{#nodeId.variableName#}}` syntax for variable references.
   - Use `{{#sys.query#}}` for system query variable in chatflow mode.

5. **Generate edges** -- Connect nodes following the rules in `references/edge-and-layout.md`:
   - Edge ID format: `{sourceId}-{sourceHandle}-{targetId}-{targetHandle}`
   - Standard `sourceHandle`: `"source"` for most nodes
   - If/Else branches: `"true"` (first case), case_id (elif), `"false"` (else)
   - Question Classifier branches: topic `id` as sourceHandle
   - `targetHandle` is always `"target"`
   - All edges use `type: "custom"` and `zIndex: 0` (or `1002` inside iterations)

6. **Calculate layout positions** -- Place nodes on a left-to-right grid:
   - Start node at `{x: 80, y: 282}`
   - Horizontal spacing: 300px per step (`NODE_WIDTH 240 + X_OFFSET 60`)
   - Vertical spacing for parallel branches: 200px apart
   - Node width: 244px, height: varies by node (54-150px typical)

7. **Output file** -- Render as YAML (default) or JSON. Validate structure completeness.

## DSL Structure Quick Reference

```yaml
version: "0.6.0"
kind: app
app:
  name: "Workflow Name"
  mode: "advanced-chat"           # or "workflow"
  description: "..."
  icon: "\U0001F916"
  icon_background: "#FFEAD5"
  icon_type: emoji
  use_icon_as_answer_icon: false
dependencies: []
workflow:
  environment_variables: []
  conversation_variables: []
  features:
    file_upload:
      enabled: false
    opening_statement: ""         # chatflow only
    retriever_resource:
      enabled: false
    sensitive_word_avoidance:
      enabled: false
    speech_to_text:
      enabled: false
    suggested_questions: []       # chatflow only
    suggested_questions_after_answer:
      enabled: false
    text_to_speech:
      enabled: false
  graph:
    nodes: []                     # Node objects
    edges: []                     # Edge objects
    viewport:
      x: 0
      y: 0
      zoom: 0.7
```

For the complete field-level specification, see `references/dsl-format.md`.

## Output Rules

- **Output location**: Final workflow files (`.dify.yml` / `.dify.json`) go to **current working directory**. Any intermediate/temp files go to `/tmp/dify-workflow/`.
- **Filename**: `<kebab-case-name>.dify.yml` (or `.dify.json` for JSON output)
- **Required sections**: `version`, `kind`, `app`, `workflow` (with `graph`, `features`)
- **Node IDs**: 13-digit timestamp strings (e.g., `"1711536487001"`). Increment by a few thousand between nodes to simulate realistic IDs.
- **Coordinates**: Start at `{x: 80, y: 282}`. Each subsequent column at `+300` on x-axis. Parallel branches offset on y-axis by `+200`.
- **Variable references**: Use `{{#nodeId.variableName#}}` syntax. System variables use `sys` prefix: `{{#sys.query#}}`, `{{#sys.user_id#}}`.
- **Model provider format**: `"langgenius/<provider>/<provider>"` (e.g., `"langgenius/openai/openai"`). Use a real, currently-shipping model name for `model.name` (e.g. `deepseek-chat`, `gpt-4o-mini`); fictional names like `deepseek-v4-pro` will fail provider validation.
- **All string node IDs** must be quoted in YAML to prevent type coercion.

## Common Schema Pitfalls (must avoid)

These are the schema mistakes most likely to make a generated DSL fail import in Dify 0.6.0:

1. **Code node language and outputs are strict in Dify 1.14.2.** Do not generate `code_language: json`. Use only `python3` or `javascript`. Code node output schema is a dict and supports only `string`, `number`, `object`, `boolean`, `array[string]`, `array[number]`, `array[object]`, or `array[boolean]`. The code must return exactly the declared output keys.
2. **Variable shape differs per node type.** The shape of a single entry inside a node's variable list is **not** the same across nodes:
   - `code`, `llm`, `template-transform`, `parameter-extractor` use **objects**:
     ```yaml
     variables:
       - variable: my_arg
         value_selector: ["upstream_id", "field"]
         value_type: string
     ```
   - `variable-aggregator` uses **bare nested lists** (no `value_selector:` wrapper, no `variable:` name):
     ```yaml
     variables:
       - - "branch1_id"
         - "output"
       - - "branch2_id"
         - "output"
     ```
   - `document-extractor` uses a singular `variable_selector` (a flat array of strings), not a list:
     ```yaml
     variable_selector: ["upstream_id", "field"]
     ```
3. **`memory` belongs only to chatflow LLM nodes.** In `workflow`-mode apps, `sys.query` does not exist; LLM nodes must omit the `memory` block. Same applies for LLM nodes placed inside an iteration in a workflow app.
4. **Iteration containers need dimensions in both places.** Set `width` and `height` both inside `data` and at the outer node level. Iteration-start child uses outer `type: custom-iteration-start` with `data.type: iteration-start`.
5. **Iteration child nodes** must declare `parentId: <iteration_id>`, `data.isInIteration: true`, `data.iteration_id: <iteration_id>`, and `zIndex: 1002`. Their `position` is relative to the iteration container (e.g. start near `{x: 24, y: 68}`).
6. **`iterator_input_type` must match the real element type.** For files: `"array[file]"`. For numbers: `"array[number]"`. Mismatch breaks runtime variable resolution.
7. **Plugin dependencies.** If you reference a model provider (e.g. `langgenius/deepseek/deepseek`) or a tool provider not bundled with Dify, declare it in top-level `dependencies` so import flags missing plugins clearly. Empty `dependencies: []` is fine when the deployment is known to have those plugins installed.
8. **End node `outputs` is a list of `{variable, value_selector, value_type}` items**; Code node `outputs` is a dict keyed by variable name with `{type, children}` values. Do not mix the two shapes.
9. **Edge `data` block** should include `sourceType`, `targetType`, `isInIteration`, `isInLoop`. Edges inside iteration also need `iteration_id` and `zIndex: 1002`.

## Template Matching

Use a template when the user's request closely matches one of these patterns. Load the template, then customize fields (model, prompts, variables) to fit the specific requirement.

| Template | Path | Matches When |
|----------|------|-------------|
| Chatbot | `references/templates/chatbot.yml` | Simple conversational bot: Start -> LLM -> Answer |
| RAG | `references/templates/rag.yml` | Knowledge-base Q&A: Start -> Knowledge Retrieval -> LLM -> Answer |
| Agent | `references/templates/agent.yml` | Tool-using agent with question classification or parameter extraction |
| Translation | `references/templates/translation.yml` | Text transformation/translation: Start -> LLM (with specific system prompt) -> Answer/End |

If the requirement partially matches, use the closest template as a starting point and add/remove nodes as needed.

## Few-Shot Example

A minimal chatflow (Start -> LLM -> Answer):

```yaml
version: "0.6.0"
kind: app
app:
  name: "Simple Chatbot"
  mode: advanced-chat
  icon: "\U0001F916"
  icon_background: "#FFEAD5"
  icon_type: emoji
  description: "A minimal chatbot using GPT-4o-mini."
  use_icon_as_answer_icon: false
dependencies: []
workflow:
  environment_variables: []
  conversation_variables: []
  features:
    file_upload:
      enabled: false
    opening_statement: "Hello! How can I help you today?"
    retriever_resource:
      enabled: false
    sensitive_word_avoidance:
      enabled: false
    speech_to_text:
      enabled: false
    suggested_questions:
      - "What can you help me with?"
    suggested_questions_after_answer:
      enabled: false
    text_to_speech:
      enabled: false
  graph:
    edges:
      - id: "1711536487001-source-1711536522001-target"
        source: "1711536487001"
        sourceHandle: source
        target: "1711536522001"
        targetHandle: target
        type: custom
        zIndex: 0
        data:
          sourceType: start
          targetType: llm
      - id: "1711536522001-source-1711536558001-target"
        source: "1711536522001"
        sourceHandle: source
        target: "1711536558001"
        targetHandle: target
        type: custom
        zIndex: 0
        data:
          sourceType: llm
          targetType: answer
    nodes:
      - id: "1711536487001"
        type: custom
        position: { x: 80, y: 282 }
        data:
          type: start
          title: Start
          desc: ""
          variables: []
      - id: "1711536522001"
        type: custom
        position: { x: 380, y: 282 }
        data:
          type: llm
          title: LLM
          desc: ""
          model:
            provider: "langgenius/openai/openai"
            name: "gpt-4o-mini"
            mode: "chat"
            completion_params:
              temperature: 0.7
          prompt_template:
            - role: "system"
              text: "You are a helpful assistant."
          variables: []
          context:
            enabled: false
            variable_selector: []
          vision:
            enabled: false
          memory:
            query_prompt_template: "{{#sys.query#}}"
            window:
              enabled: false
              size: 10
      - id: "1711536558001"
        type: custom
        position: { x: 680, y: 282 }
        data:
          type: answer
          title: Answer
          desc: ""
          answer: "{{#1711536522001.text#}}"
          variables: []
    viewport:
      x: 0
      y: 0
      zoom: 0.7
```

For a complete version with all optional fields, see `examples/simple-chatbot.yml`. For a RAG workflow example, see `examples/rag-with-rerank.yml`.

## Format Selection

- **YAML (default)**: Output as `.dify.yml`. Use standard YAML formatting with 2-space indentation. Quote all node ID strings. This is the preferred format for readability and Dify import.
- **JSON**: Output as `.dify.json` when the user explicitly requests JSON. Use the same structure with standard JSON formatting. Useful for programmatic consumption or API-based import.

Both formats are fully supported by Dify's import functionality.
