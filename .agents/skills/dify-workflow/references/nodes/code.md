# Code (`code`)

## Purpose
Execute custom Python 3 or JavaScript code with mapped input variables and structured output.

This reference is patched for Dify 1.14.2 / App DSL 0.6.0. The backend Code node schema is stricter than the broad frontend `VarType` enum: do not generate unsupported Code languages or unsupported Code output types.

## Core Fields
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code_language` | `CodeLanguage` | Yes | Language to execute: `python3` or `javascript` |
| `code` | `string` | Yes | The source code to run. Must define a `main` function (Python/JS) that returns an object |
| `variables` | `Variable[]` | Yes | Input variable mappings passed as arguments to the `main` function |
| `outputs` | `OutputVar` | Yes | Declared output variable schema. Keys must exactly match the object returned by `main()` |

## Full Schema

### CodeNodeType (extends CommonNodeType)

```typescript
{
  // --- CommonNodeType fields ---
  title: string               // Display name of the node
  desc: string                // Description of the node
  type: 'code'                // BlockEnum.Code

  // --- Code-specific fields ---
  variables: Variable[]       // Input variable mappings
  code_language: CodeLanguage // 'python3' | 'javascript'
  code: string                // Source code to execute
  outputs: OutputVar          // Output variable declarations

  // --- Optional CommonNodeType fields ---
  error_strategy?: ErrorHandleTypeEnum  // 'terminated' | 'continue-on-error' | 'remove-abnormal-output'
  retry_config?: {
    retry_enabled: boolean
    max_retries: number
    retry_interval: number
  }
}
```

### CodeLanguage (enum)
| Value | Description |
|-------|-------------|
| `python3` | Python 3 code |
| `javascript` | JavaScript code |

Do not use `json`. Dify 1.14.2's backend CodeNodeData only accepts `python3` and `javascript`.

### Variable
```typescript
{
  variable: string            // Name used in code (e.g., parameter name in main())
  value_selector: string[]    // Reference path: [nodeId, ...keyPath]
  value_type?: VarType        // Optional type hint
  required?: boolean          // Whether the variable is required
}
```

### OutputVar
A `Record<string, { type: CodeOutputType, children: null | Record<string, OutputVar> }>` where each key is an output variable name.

#### Supported Code output types in Dify 1.14.2
`string`, `number`, `boolean`, `object`, `array[string]`, `array[number]`, `array[object]`, `array[boolean]`

Do not use these broader Dify variable types in Code node `outputs`: `integer`, `secret`, `file`, `array`, `array[file]`, `any`, `array[any]`, `none`, `group`.

## Variable Reference Rules

**Inputs:** Each entry in `variables` maps a variable name to a value from an upstream node via `value_selector`. The variable name corresponds to a parameter of the `main()` function in the code.

**Outputs:** Declared in the `outputs` field as key-type pairs. Downstream nodes reference these as `[thisNodeId, outputKey]`. The `main()` function must return an object whose keys exactly match the declared output keys: every declared key must be returned, and no undeclared keys may be returned.

## Default Values
```json
{
  "code": "",
  "code_language": "python3",
  "variables": [],
  "outputs": {}
}
```

## Validation Rules
- Every entry in `variables` must have a non-empty `variable` name
- Every entry in `variables` must have a non-empty `value_selector`
- `code` must be non-empty
- `code_language` must be `python3` or `javascript`
- `outputs` must be a dict, not a list
- Returned output keys must exactly match `outputs`
- Returned values must match the declared output types
- Object and array values must stay within Dify's runtime limits; deeply nested objects and oversized strings/numbers fail at runtime

## Runtime Failure Examples

These examples may import but fail when the node runs:

```python
# Missing declared key "result"
def main(text: str) -> dict:
    return {"answer": text}

# Extra undeclared key "debug"
def main(text: str) -> dict:
    return {"result": text, "debug": True}

# Type mismatch: outputs.result declares string
def main(text: str) -> dict:
    return {"result": 123}
```

Prefer writing the `outputs` schema first, then writing `main()` so the returned object mirrors it exactly.

## Example Snippet

```yaml
- data:
    title: Transform Text
    desc: Convert text to uppercase
    type: code
    code_language: python3
    variables:
      - variable: input_text
        value_selector:
          - "start_node_id"
          - text
    code: |
      def main(input_text: str) -> dict:
          return {
              "result": input_text.upper()
          }
    outputs:
      result:
        type: string
        children: null
  id: code-node-1
  position:
    x: 400
    y: 200
```
