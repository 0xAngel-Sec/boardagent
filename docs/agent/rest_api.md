# TaskManager REST API (agent docs)

Base URL: `http://127.0.0.1:7373`

## Endpoints

### GET /healthz

Healthz

- **200**: Successful Response

### POST /tasks

Create Task

**Request body schema:** `{"$ref": "#/components/schemas/TaskCreate"}`

- **201**: Successful Response
- **422**: Validation Error

### GET /tasks

List Tasks

- **200**: Successful Response
- **422**: Validation Error

### GET /tasks/schema/priority

Priority Values

- **200**: Successful Response

### GET /tasks/schema/status

Status Values

- **200**: Successful Response

### GET /tasks/{task_id}

Get Task

- **200**: Successful Response
- **422**: Validation Error

### PATCH /tasks/{task_id}

Update Task

**Request body schema:** `{"$ref": "#/components/schemas/TaskUpdate"}`

- **200**: Successful Response
- **422**: Validation Error

### DELETE /tasks/{task_id}

Delete Task

- **204**: Successful Response
- **422**: Validation Error

### POST /tasks/{task_id}/claim

Claim Task

**Request body schema:** `{"$ref": "#/components/schemas/TaskClaim"}`

- **200**: Successful Response
- **422**: Validation Error

### POST /tasks/{task_id}/complete

Complete Task

**Request body schema:** `{"$ref": "#/components/schemas/TaskComplete"}`

- **200**: Successful Response
- **422**: Validation Error

