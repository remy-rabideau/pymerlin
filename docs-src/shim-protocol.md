# PyMerlin Shim Protocol

Newline-delimited JSON over stdin/stdout between the Java shim JAR and the Python server process.

- Each message is a single JSON object followed by `\n`.
- Java writes to Python's stdin; Python writes to Java's stdout.
- The exchange is strictly request/response — Java always sends first, Python always responds.
- All durations are in **microseconds** (integer).

---

## Startup

Java launches:
```
python -m pymerlin._server --model <path/to/model.py:ClassName>
```

Python writes a single ready message to stdout when initialised:
```json
{"op": "ready"}
```

---

## Message Reference

### 1. `get_activity_types`

Java asks Python to describe every activity type in the model.

**Request (Java → Python)**
```json
{"op": "get_activity_types"}
```

**Response (Python → Java)**
```json
{
  "op": "activity_types",
  "types": {
    "increment_counter": {
      "parameters": {
        "count": {"type": "int", "required": false, "default": 1}
      }
    },
    "temperature_cycle": {
      "parameters": {}
    }
  }
}
```

Parameter `type` values: `"int"`, `"float"`, `"str"`, `"bool"`, `"any"`.

---

### 2. `get_resources`

Java asks Python to describe every registered resource.

**Request**
```json
{"op": "get_resources"}
```

**Response**
```json
{
  "op": "resources",
  "resources": {
    "/counter":     {"value_type": "int"},
    "/temperature": {"value_type": "float"},
    "/status":      {"value_type": "str"}
  }
}
```

---

### 3. `get_resource_value`

Java queries the current value of a single resource (used during Aerie resource extraction).

**Request**
```json
{"op": "get_resource_value", "name": "/counter"}
```

**Response**
```json
{"op": "resource_value", "name": "/counter", "value": "0"}
```

Value is always serialised as a string.

---

### 4. `run_activity`

Java tells Python to start executing a named activity. Python begins running it and immediately
suspends at the first `delay()`, `wait_until()`, `emit()`, `spawn()`, or completion, then
sends the appropriate response.

**Request**
```json
{"op": "run_activity", "id": "act-1", "name": "increment_counter", "args": {"count": 3}}
```

`id` is an opaque string Java uses to identify concurrent activities.

---

### 5. `resume`

Java resumes a suspended activity after honouring its last yield (delay elapsed, condition met, etc.).

**Request**
```json
{"op": "resume", "id": "act-1"}
```

After `resume`, Python sends the next yield response (same set as after `run_activity`).

---

## Activity Yield Responses (Python → Java)

These are sent in response to `run_activity` or `resume`.

### `delay`
Activity called `delay(duration)` and is suspended.
```json
{"op": "delay", "id": "act-1", "duration_us": 600000000}
```

### `emit`
Activity called `cell.emit(value)`. Java applies it to the Aerie cell, then immediately
sends another `resume` so Python can continue to the next yield point.
```json
{"op": "emit", "id": "act-1", "resource": "/counter", "value": "1"}
```

### `spawn`
Activity called `spawn(child_activity(...))`. Java schedules the child, then immediately
sends another `resume` so the parent continues.
```json
{"op": "spawn", "id": "act-1", "name": "increment_counter", "args": {}}
```

### `wait_until`
Activity called `wait_until(condition)`. Java polls the condition each sim tick and sends
`resume` when it evaluates to true.
```json
{"op": "wait_until", "id": "act-1", "condition_resource": "/temperature", "condition_op": "gt", "condition_value": "30.0"}
```

Supported `condition_op` values: `"gt"`, `"lt"`, `"gte"`, `"lte"`, `"eq"`, `"neq"`.

> **Note:** For complex/lambda conditions not expressible as a simple comparison, Python can
> instead periodically re-evaluate the condition itself. In that case it sends:
> ```json
> {"op": "wait_until_opaque", "id": "act-1"}
> ```
> Java calls `resume` each sim tick; Python re-evaluates the condition and either sends
> another `wait_until_opaque` (still waiting) or the next real yield point.

### `done`
Activity completed normally.
```json
{"op": "done", "id": "act-1"}
```

### `error`
Activity raised an exception.
```json
{"op": "error", "id": "act-1", "message": "ZeroDivisionError: division by zero"}
```

---

## Error handling

If Python sends `error` for an activity, Java logs it and treats the activity span as failed.
If the Python process crashes or closes stdout, Java shuts down simulation with a fatal error.

---

## Concurrency model

Python executes one activity step at a time (single-threaded cooperative). Java serialises
`run_activity` / `resume` calls — it never sends a new request until it has received a yield
response for the previous one. This means no locking is needed in the Python server.

---

## Lifecycle

```
Java                          Python
 |                              |
 |-- (spawn process) ---------->|
 |<-- {"op":"ready"} -----------|
 |                              |
 |-- get_activity_types ------->|
 |<-- activity_types -----------|
 |                              |
 |-- get_resources ------------>|
 |<-- resources ---------------|
 |                              |
 |  [ simulation begins ]       |
 |                              |
 |-- run_activity id=act-1 ---->|
 |<-- delay 600s ---------------|
 |                              |
 |  [ 600s elapses in sim ]     |
 |                              |
 |-- resume id=act-1 ---------->|
 |<-- emit /counter "1" --------|
 |-- resume id=act-1 ---------->|
 |<-- spawn increment_counter --|
 |-- resume id=act-1 ---------->|
 |<-- done id=act-1 -----------|
 |                              |
 |  [ Java schedules child ]    |
 |-- run_activity id=act-2 ---->|
 |   ...                        |
```
