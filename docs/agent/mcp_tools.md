# TaskManager MCP Tools (agent docs)

Transport: stdio. Command: `taskmanager-mcp`

## Tools

### taskmanager_create_task

Create a new task. Priority is a color: red/orange/yellow/green/blue/white.

```json
{
  "type": "object",
  "properties": {
    "title": {
      "type": "string",
      "description": "Task title"
    },
    "description": {
      "type": "string"
    },
    "due": {
      "type": "string",
      "format": "date-time"
    },
    "priority": {
      "type": "string",
      "enum": [
        "red",
        "orange",
        "yellow",
        "green",
        "blue",
        "white"
      ],
      "default": "white"
    },
    "project": {
      "type": "string"
    },
    "status": {
      "type": "string",
      "enum": [
        "todo",
        "in_progress",
        "blocked",
        "done"
      ],
      "default": "todo"
    },
    "agent_id": {
      "type": "string",
      "description": "Agent namespace for metadata"
    },
    "metadata": {
      "type": "object",
      "description": "Freeform JSON metadata"
    }
  },
  "required": [
    "title"
  ]
}
```

### taskmanager_list_tasks

List tasks, optionally filtered.

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string",
      "enum": [
        "todo",
        "in_progress",
        "blocked",
        "done"
      ]
    },
    "project": {
      "type": "string"
    },
    "owner": {
      "type": "string"
    }
  }
}
```

### taskmanager_get_task

Read a single task by id.

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "integer"
    }
  },
  "required": [
    "id"
  ]
}
```

### taskmanager_update_task

Update a task. Metadata is merged into agent_id's namespace.

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "integer"
    },
    "title": {
      "type": "string"
    },
    "description": {
      "type": "string"
    },
    "due": {
      "type": "string",
      "format": "date-time"
    },
    "priority": {
      "type": "string",
      "enum": [
        "red",
        "orange",
        "yellow",
        "green",
        "blue",
        "white"
      ]
    },
    "project": {
      "type": "string"
    },
    "status": {
      "type": "string",
      "enum": [
        "todo",
        "in_progress",
        "blocked",
        "done"
      ]
    },
    "agent_id": {
      "type": "string"
    },
    "metadata": {
      "type": "object"
    }
  },
  "required": [
    "id"
  ]
}
```

### taskmanager_delete_task

Delete a task by id.

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "integer"
    }
  },
  "required": [
    "id"
  ]
}
```

### taskmanager_claim_task

Claim/lock a todo task for an agent. Returns an error if unavailable.

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "integer"
    },
    "agent_id": {
      "type": "string"
    }
  },
  "required": [
    "id",
    "agent_id"
  ]
}
```

### taskmanager_complete_task

Mark a task done. Only the owning agent may complete it.

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "integer"
    },
    "agent_id": {
      "type": "string"
    }
  },
  "required": [
    "id",
    "agent_id"
  ]
}
```

