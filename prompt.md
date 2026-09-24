# Azure OpenAI Agentic Continuous Internal Audit KRI Engine

## 1. Role and Objective

Act as a senior Python backend engineer, Generative AI engineer, Agentic
AI architect, and database engineer.

Build a backend-only Proof of Concept (POC) for a configurable
Continuous Internal Audit Key Risk Indicator (KRI) Engine.

Use Azure OpenAI function/tool calling as the orchestration layer. The
LLM interprets administrator-defined KRI test steps and selects
registered tools. The tools execute controlled, deterministic business
operations.

This is **Person A's responsibility**.

Person B is building the frontend and will consume the backend APIs.

### Do not build

-   React frontend
-   Streamlit UI
-   HTML pages
-   Frontend components
-   Frontend styling
-   Separate UI application

Focus on:

-   Backend architecture
-   SQLite database
-   KRI configuration
-   Agent orchestration
-   Tool registry
-   Deterministic audit logic
-   Evidence generation
-   REST APIs
-   Testing
-   Documentation

------------------------------------------------------------------------

## 2. Technology Stack

Use:

-   Python 3.11+
-   FastAPI
-   SQLite
-   SQLAlchemy 2.x
-   Pydantic v2
-   Azure OpenAI
-   Official OpenAI Python SDK
-   Pytest
-   Uvicorn
-   python-dotenv or Pydantic Settings

Use a modular architecture, but avoid unnecessary microservices and
distributed infrastructure for this POC.

Use Azure OpenAI through an abstraction so the provider can be replaced
later.

Use the Azure OpenAI `AzureOpenAI` client and a supported
function/tool-calling chat-completions flow for the initial
implementation.

------------------------------------------------------------------------

## 3. Business Problem

Internal audit teams periodically review financial transactions.
Problems may be identified weeks or months after the transaction
occurred.

The goal is to build a configurable KRI engine that continuously
evaluates financial records, identifies exceptions, calculates metrics,
and produces evidence for human review.

### Primary KRI

**Order Intake vs Purchase Order**

Every customer order booked in SAP ECC should have an appropriate
corresponding Purchase Order (PO).

The system must identify:

1.  Exact or acceptable matches
2.  Amount mismatches beyond the configured threshold
3.  Orders without a matching PO
4.  Missing or invalid matching identifiers
5.  Duplicate or ambiguous matches
6.  Other supported validation exceptions

Use 80 deterministic mock order records for the POC.

The system must calculate metrics from the actual dataset. Do not
hardcode the expected number of exceptions.

------------------------------------------------------------------------

## 4. KRI Configuration

The backend must support the fields represented in the administrator's
KRI creation form.

### Basic KRI fields

-   `identifier`
-   `name`
-   `process_area`
-   `indicator_type`
-   `risk_description`
-   `end_goal`

### Process area

Seed predefined process areas, including:

-   O2C (Order to Cash)

### Indicator type

Support:

-   `LEADING`
-   `LAGGING`

### KRI status

Support:

-   `DRAFT`
-   `IN_ASSESSMENT`
-   `ACTIVE`
-   `INACTIVE`
-   `ARCHIVED`

A KRI must be validated before activation.

------------------------------------------------------------------------

## 5. Data Sources

Seed the following data source catalog:

1.  SAP ECC
2.  Sapiens
3.  Red Box / PO
4.  Blue Planet
5.  EMS / SharePoint

Each KRI can have multiple data sources.

Use a many-to-many relationship between KRI and data sources.

For this POC:

-   Use SQLite mock data.
-   Do not connect to real SAP ECC or other enterprise systems.
-   Store the source system associated with every record.
-   Create an extensible connector abstraction for future integrations.

------------------------------------------------------------------------

## 6. Administrator-Defined Test Steps

Administrators can create multiple test steps using natural language.

Example:

1.  Extract the population for the period.
2.  Match order intake records with purchase orders.
3.  Compare order amounts with PO amounts.
4.  Flag mismatches beyond 10%.
5.  Prepare evidence and explanations.

Each test step must include:

-   `id`
-   `kri_id`
-   `step_number`
-   `title`
-   `instruction`
-   `is_active`
-   `created_at`
-   `updated_at`

Requirements:

-   Preserve the original administrator instruction.
-   Execute steps in `step_number` order.
-   Allow adding, updating, deleting, and reordering steps.
-   Do not hardcode one fixed workflow.
-   Support future KRIs with different steps.

------------------------------------------------------------------------

## 7. Azure OpenAI Tool-Calling Architecture

### Core principle

The LLM interprets the KRI instructions and selects tools.

The tools execute actual operations.

The LLM must not:

-   Execute arbitrary Python.
-   Execute arbitrary SQL.
-   Execute shell commands.
-   Directly modify financial source records.
-   Invent financial values.
-   Override deterministic calculation results.
-   Bypass tool validation.
-   Invoke tools outside the registered allowlist.

### Execution flow

1.  Load the KRI configuration.
2.  Load ordered administrator-defined test steps.
3.  Load available registered tools.
4.  Ask Azure OpenAI to interpret the steps and orchestrate the
    workflow.
5.  Receive a tool call from Azure OpenAI.
6.  Validate the tool name.
7.  Validate the tool arguments with Pydantic.
8.  Check dependencies and execution permissions.
9.  Execute the Python tool handler.
10. Persist the tool invocation and result metadata.
11. Return the structured result to Azure OpenAI.
12. Continue until the workflow is complete.
13. Validate the final audit result.
14. Persist metrics, exceptions, evidence, and execution traces.

Implement:

-   Maximum tool-call limit per audit run
-   Maximum execution time
-   Tool allowlisting
-   Dependency validation
-   Explicit termination conditions
-   Safe failure handling
-   Retry policy for transient LLM failures
-   Infinite-loop prevention

The first POC can use synchronous execution.

------------------------------------------------------------------------

## 8. Azure OpenAI Configuration

Use environment variables:

``` env
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=
AZURE_OPENAI_DEPLOYMENT_NAME=
```

Important:

-   `AZURE_OPENAI_DEPLOYMENT_NAME` is the Azure deployment identifier.
-   Never hardcode credentials.
-   Never log API keys.
-   Use Pydantic Settings for configuration.
-   Fail with a clear configuration error if required settings are
    missing.

Example:

``` python
from openai import AzureOpenAI

client = AzureOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    api_version=settings.azure_openai_api_version,
)
```

Create an abstraction:

``` python
class LLMProvider(Protocol):
    def create_response(self, messages: list, tools: list) -> dict:
        ...
```

The Azure OpenAI provider should support:

-   Tool-call responses
-   Text responses
-   Configurable deployment name
-   Configurable temperature where supported
-   Error handling
-   Retry handling
-   Token and latency metadata where available

------------------------------------------------------------------------

## 9. Tool Registry

Create a centralized and secure tool registry.

Each tool must define:

-   Tool name
-   Description
-   Input schema
-   Output schema
-   Handler
-   Version
-   Enabled status
-   Allowed execution context

Example:

``` python
class RegisteredTool:
    name: str
    description: str
    input_model: type
    output_model: type
    handler: Callable
    version: str
    enabled: bool
```

Requirements:

-   Use Pydantic models for all tool inputs and outputs.
-   Allow only registered and enabled tools.
-   Generate Azure OpenAI tool schemas from controlled definitions.
-   Do not allow dynamic imports.
-   Do not allow arbitrary function execution.
-   Do not allow the LLM to select unrestricted database tables.
-   Do not allow arbitrary SQL.
-   Keep the registry extensible for future KRIs.

------------------------------------------------------------------------

## 10. Required Tools

### Tool 1: `fetch_financial_data`

#### Purpose

Fetch financial records from an approved source.

#### Supported sources

-   `SAP_ECC`
-   `RED_BOX_PO`

#### Example input

``` json
{
  "source_system": "SAP_ECC",
  "entity": "ORDER_INTAKE",
  "start_date": "2026-01-01",
  "end_date": "2026-03-31",
  "filters": {}
}
```

#### Behavior

-   Validate the source system.
-   Validate the entity against an allowlist.
-   Query SQLite using controlled repository methods.
-   Apply supported filters.
-   Return structured records or a scoped dataset reference.
-   Do not allow arbitrary SQL.
-   Apply safe record limits or pagination.
-   Preserve source-system metadata.

#### Example output

``` json
{
  "tool_name": "fetch_financial_data",
  "status": "SUCCESS",
  "dataset_reference": "dataset_orders_001",
  "entity": "ORDER_INTAKE",
  "record_count": 80,
  "records": []
}
```

Use dataset references for large data instead of repeatedly sending full
records through the LLM context.

------------------------------------------------------------------------

### Tool 2: `compare_records`

#### Purpose

Match financial records and identify comparison results.

#### Example input

``` json
{
  "left_dataset_reference": "dataset_orders_001",
  "right_dataset_reference": "dataset_pos_001",
  "match_configuration": {
    "left_field": "order_id",
    "right_field": "order_id",
    "fallback_field": "po_reference"
  }
}
```

#### Behavior

-   Load datasets from the current audit execution context.
-   Validate matching fields against an allowlist.
-   Match records deterministically.
-   Identify matched records.
-   Identify unmatched orders.
-   Identify missing POs.
-   Identify duplicate or ambiguous matches.
-   Preserve source record references.
-   Store comparison results under the current audit run.

#### Output must include

-   Comparison reference
-   Matched count
-   Unmatched count
-   Ambiguous count
-   Structured comparison results
-   Exception candidates

Do not accept arbitrary Python expressions or SQL.

------------------------------------------------------------------------

### Tool 3: `calculate_difference`

#### Purpose

Calculate amount differences deterministically.

#### Example input

``` json
{
  "left_value": 120000,
  "right_value": 100000,
  "calculation_type": "ABSOLUTE_PERCENTAGE_DIFFERENCE",
  "denominator_policy": "RIGHT_VALUE_ABSOLUTE"
}
```

#### Behavior

-   Validate numeric inputs.
-   Handle missing values.
-   Handle zero denominators using an explicit policy.
-   Return deterministic results.
-   Do not execute LLM-generated formulas.
-   Document the calculation policy.

#### Example output

``` json
{
  "status": "SUCCESS",
  "difference": 20000,
  "absolute_difference": 20000,
  "difference_percentage": 20
}
```

------------------------------------------------------------------------

### Tool 4: `apply_threshold`

#### Purpose

Apply the configured KRI threshold.

#### Example input

``` json
{
  "value": 20,
  "threshold_type": "PERCENTAGE_DIFFERENCE",
  "operator": ">",
  "threshold_value": 10
}
```

#### Behavior

-   Validate threshold type.
-   Validate the operator.
-   Use the active KRI's configured threshold.
-   Apply deterministic comparison logic.
-   Return a reason code and decision.
-   Do not allow the LLM to override the result.

#### Example output

``` json
{
  "is_exception": true,
  "reason_code": "AMOUNT_MISMATCH",
  "actual_value": 20,
  "threshold_value": 10,
  "operator": ">"
}
```

Do not hardcode threshold values in the handler.

------------------------------------------------------------------------

### Tool 5: `calculate_kri_metrics`

#### Purpose

Calculate aggregate KRI metrics.

#### Example input

``` json
{
  "audit_run_reference": "run_001",
  "calculation_policy": {
    "mismatch_value_definition": "ORDER_VALUE_OF_QUALIFYING_EXCEPTIONS"
  }
}
```

#### Behavior

-   Calculate metrics from validated transaction-level results.
-   Avoid double-counting transactions.
-   Include missing PO exceptions according to the configured scoring
    policy.
-   Return metric definitions and values.
-   Keep all calculations deterministic.

#### Metrics

Return:

-   Total transaction count
-   Total order value
-   Matched count
-   Missing PO count
-   Amount mismatch count
-   Total exception count
-   Exception value
-   Mismatch value percentage
-   Exception rate by count
-   Exception rate by value
-   Counts by exception type
-   Counts by severity

Document every metric definition.

------------------------------------------------------------------------

### Tool 6: `build_evidence`

#### Purpose

Build evidence for each flagged transaction.

#### Example input

``` json
{
  "audit_run_reference": "run_001",
  "exception_references": [
    "exception_001"
  ]
}
```

#### Behavior

-   Retrieve the relevant source records.
-   Return the complete order record.
-   Return the complete matching PO record where available.
-   Include calculation details.
-   Include configured threshold values.
-   Include source record identifiers.
-   Preserve provenance.
-   Support missing PO evidence.

For missing POs, include the order record and clearly state that no
matching PO was found.

Never fabricate evidence.

------------------------------------------------------------------------

### Tool 7: `generate_explanation`

#### Purpose

Generate a plain-English explanation based only on verified results.

Use deterministic templates for known exception types.

Optionally use Azure OpenAI for improved readability, but keep the
structured reason code and numerical values authoritative.

#### Example input

``` json
{
  "exception_type": "AMOUNT_MISMATCH",
  "calculation": {
    "order_amount": 120000,
    "po_amount": 100000,
    "difference_percentage": 20,
    "threshold": 10
  },
  "evidence_reference": "evidence_001"
}
```

#### Example output

``` text
The order amount is 20% higher than the PO amount, exceeding the configured 10% threshold.
```

Requirements:

-   Do not change numerical values.
-   Do not invent facts.
-   Keep reason codes separate from generated text.
-   Validate any LLM-generated explanation against the structured
    evidence.
-   Prefer deterministic templates for the POC.

------------------------------------------------------------------------

## 11. Execution Context

Each audit run must have an isolated execution context.

Use references for datasets and intermediate results instead of
repeatedly passing large records through the LLM.

Example:

``` json
{
  "audit_run_id": "run_001",
  "datasets": {
    "dataset_orders_001": {
      "record_count": 80
    },
    "dataset_pos_001": {
      "record_count": 75
    }
  },
  "tool_results": [],
  "exceptions": [],
  "metrics": {}
}
```

The execution context must:

-   Be scoped to the current audit run.
-   Prevent cross-run data access.
-   Track tool outputs.
-   Track intermediate dataset references.
-   Support evidence retrieval.
-   Support audit traceability.

Store intermediate results in controlled database tables or a scoped
execution store.

------------------------------------------------------------------------

## 12. Agent Execution Loop

Create:

``` text
app/services/agent_orchestrator.py
```

Responsibilities:

1.  Load the active KRI configuration.
2.  Load ordered test steps.
3.  Load or generate the structured execution plan.
4.  Validate the execution plan.
5.  Build the tool definitions.
6.  Call Azure OpenAI.
7.  Parse tool calls.
8.  Validate tool arguments with Pydantic.
9.  Resolve the registered tool.
10. Validate dependencies.
11. Execute the tool handler.
12. Store invocation metadata.
13. Return tool output to Azure OpenAI.
14. Continue until completion or execution limits are reached.
15. Validate the final audit result.
16. Persist metrics, exceptions, evidence, and trace information.

The application must process tool calls explicitly. Do not assume that
Azure OpenAI executes Python tools automatically.

------------------------------------------------------------------------

## 13. Structured Execution Plan

The LLM may translate natural-language test steps into a structured
plan.

Example:

``` json
{
  "steps": [
    {
      "step_number": 1,
      "operation": "EXTRACT_POPULATION",
      "tool_name": "fetch_financial_data",
      "parameters": {
        "source_system": "SAP_ECC",
        "entity": "ORDER_INTAKE"
      }
    },
    {
      "step_number": 2,
      "operation": "MATCH_RECORDS",
      "tool_name": "compare_records",
      "parameters": {
        "left_field": "order_id",
        "right_field": "order_id"
      }
    },
    {
      "step_number": 3,
      "operation": "COMPARE_AMOUNTS",
      "tool_name": "calculate_difference",
      "parameters": {
        "calculation_type": "ABSOLUTE_PERCENTAGE_DIFFERENCE"
      }
    },
    {
      "step_number": 4,
      "operation": "APPLY_THRESHOLD",
      "tool_name": "apply_threshold"
    },
    {
      "step_number": 5,
      "operation": "BUILD_EVIDENCE",
      "tool_name": "build_evidence"
    }
  ]
}
```

Validate:

-   Supported operation
-   Registered tool
-   Required parameters
-   Allowed fields
-   Dependency order
-   Threshold references
-   Data source permissions

The plan must be versioned and stored with the audit run.

Do not execute arbitrary instructions that cannot be mapped to a
supported operation.

For audit-critical operations, enforce mandatory backend stages even if
the LLM does not request them.

------------------------------------------------------------------------

## 14. Database Design

Use one SQLite database with multiple tables.

Create at least:

1.  `kris`
2.  `process_areas`
3.  `data_sources`
4.  `kri_data_sources`
5.  `kri_test_steps`
6.  `kri_thresholds`
7.  `kri_schedules`
8.  `reviewers`
9.  `order_intake`
10. `purchase_orders`
11. `audit_runs`
12. `audit_exceptions`
13. `audit_tool_invocations`
14. `execution_datasets`
15. `execution_plans`
16. `evidence_records`

Use SQLAlchemy relationships and appropriate indexes.

Persist:

-   KRI configuration
-   Ordered test steps
-   Threshold configuration
-   Schedule configuration
-   Audit runs
-   Exceptions
-   Tool invocation trace
-   Execution plan snapshot
-   Evidence references
-   Review statuses

Use database constraints and application-level validation.

------------------------------------------------------------------------

## 15. KRI Configuration APIs

Implement:

``` text
POST   /api/v1/kris
GET    /api/v1/kris
GET    /api/v1/kris/{kri_id}
PUT    /api/v1/kris/{kri_id}
PATCH  /api/v1/kris/{kri_id}/status
POST   /api/v1/kris/{kri_id}/validate
POST   /api/v1/kris/{kri_id}/generate-plan
```

### Test step APIs

``` text
POST   /api/v1/kris/{kri_id}/steps
PUT    /api/v1/kris/{kri_id}/steps/{step_id}
DELETE /api/v1/kris/{kri_id}/steps/{step_id}
PATCH  /api/v1/kris/{kri_id}/steps/reorder
```

### Threshold APIs

Implement create, update, retrieve, and delete operations.

### Schedule APIs

Implement create, update, and retrieve operations.

Do not implement automatic scheduled execution in this POC. Store the
configuration for future use.

------------------------------------------------------------------------

## 16. Audit Execution APIs

### Run audit

``` text
POST /api/v1/audits/run
```

Request:

``` json
{
  "kri_id": 1,
  "start_date": "2026-01-01",
  "end_date": "2026-03-31",
  "customer_name": null
}
```

Behavior:

-   Validate the KRI.
-   Validate the execution plan.
-   Create an audit run.
-   Execute the agent.
-   Persist results.
-   Return structured metrics and exceptions.

Synchronous execution is acceptable for the POC.

### Retrieve audit

``` text
GET /api/v1/audits/{run_id}
```

Return:

-   Audit run status
-   KRI metadata
-   Metrics
-   Exception summary
-   Execution metadata

### Retrieve exceptions

``` text
GET /api/v1/audits/{run_id}/exceptions
GET /api/v1/audits/{run_id}/exceptions/{exception_id}
```

Support filtering by:

-   Exception type
-   Severity
-   Status
-   Order ID

The detail endpoint must return:

-   Exception type
-   Reason code
-   Calculation
-   Evidence
-   Explanation
-   Review status

### Retrieve tool trace

``` text
GET /api/v1/audits/{run_id}/trace
```

Return a sanitized tool invocation trace.

Do not expose unrestricted tool execution endpoints.

------------------------------------------------------------------------

## 17. Scheduling and Review Configuration

Store scheduling configuration from the KRI creation form.

Fields:

-   Run frequency
-   Fetch data delay days
-   Align to close calendar
-   Population percentage

Do not run scheduled jobs in this POC.

Store reviewer configuration:

-   Reviewer
-   Human review setting
-   Lifecycle status

Person B will manage the frontend review experience.

The backend should support exception status updates through APIs where
appropriate.

------------------------------------------------------------------------

## 18. Mock Data

Create deterministic mock data using a fixed random seed.

Generate at least:

-   80 order intake records
-   Corresponding purchase orders
-   Exact matches
-   Minor amount differences within the threshold
-   Major amount differences beyond the threshold
-   Missing POs
-   Invalid or missing identifiers
-   Optional duplicate or ambiguous matching cases

Use realistic but clearly synthetic data.

Create:

``` text
scripts/seed_data.py
scripts/seed_kri.py
scripts/reset_database.py
```

Do not hardcode expected exception counts.

Add a way to reset and reseed the database.

------------------------------------------------------------------------

## 19. Audit Evidence Requirements

For every exception, store:

-   Audit run ID
-   Exception ID
-   Transaction/order ID
-   Exception type
-   Severity
-   Reason code
-   Order record
-   PO record, if available
-   Calculation details
-   Threshold details
-   Evidence reference
-   Plain-English explanation
-   Source system
-   Source record identifiers
-   Created timestamp
-   Review status

Evidence must be reproducible from persisted source data and
deterministic calculations.

------------------------------------------------------------------------

## 20. Security and Governance

Implement:

-   Environment-based configuration
-   Secret protection
-   Pydantic validation
-   Tool allowlisting
-   No arbitrary code execution
-   No unrestricted SQL
-   Parameterized queries
-   Scoped execution contexts
-   Audit traceability
-   LLM tool-call limits
-   Safe error handling
-   Secret redaction in logs

The LLM must not modify financial source records.

Deterministic tool results are authoritative for financial comparisons,
thresholds, and metrics.

------------------------------------------------------------------------

## 21. Testing

Write comprehensive tests.

### Unit tests

Test:

-   Tool input validation
-   Database fetch behavior
-   Matching logic
-   Amount calculations
-   Threshold evaluation
-   Metric calculation
-   Evidence generation
-   Explanation generation
-   Execution plan validation
-   Tool registry behavior

### Agent tests

Mock Azure OpenAI responses and test:

-   Correct tool selection
-   Multiple sequential tool calls
-   Invalid tool names
-   Invalid tool arguments
-   Tool execution failures
-   Maximum tool-call limits
-   Missing dependencies
-   Infinite-loop prevention
-   Final output validation

### Integration tests

Test:

1.  Create a KRI.
2.  Add data sources.
3.  Add multiple test steps.
4.  Configure thresholds.
5.  Generate and validate the plan.
6.  Activate the KRI.
7.  Run the audit.
8.  Persist results.
9.  Retrieve metrics.
10. Retrieve exceptions.
11. Retrieve evidence.
12. Retrieve the tool trace.
13. Verify historical audit runs remain intact.

Mock Azure OpenAI in automated tests.

Tests must not require live Azure credentials.

------------------------------------------------------------------------

## 22. Project Structure

Use a clean structure similar to:

``` text
app/
├── main.py
├── core/
│   ├── config.py
│   ├── database.py
│   └── security.py
├── models/
│   ├── kri.py
│   ├── data_source.py
│   ├── transaction.py
│   ├── purchase_order.py
│   ├── audit_run.py
│   ├── exception.py
│   └── tool_invocation.py
├── schemas/
│   ├── kri.py
│   ├── audit.py
│   ├── tools.py
│   └── common.py
├── repositories/
│   ├── kri_repository.py
│   ├── financial_repository.py
│   └── audit_repository.py
├── tools/
│   ├── registry.py
│   ├── base.py
│   ├── fetch_financial_data.py
│   ├── compare_records.py
│   ├── calculate_difference.py
│   ├── apply_threshold.py
│   ├── calculate_kri_metrics.py
│   ├── build_evidence.py
│   └── generate_explanation.py
├── services/
│   ├── agent_orchestrator.py
│   ├── llm_provider.py
│   ├── execution_context.py
│   ├── plan_service.py
│   └── audit_service.py
├── api/
│   └── v1/
│       ├── kri_routes.py
│       └── audit_routes.py
└── prompts/
    ├── agent_system_prompt.txt
    └── planner_prompt.txt

scripts/
├── seed_data.py
├── seed_kri.py
└── reset_database.py

tests/
├── unit/
├── integration/
└── fixtures/
```

Adapt the structure if the existing project already has an established
organization.

Inspect the existing project before creating or modifying files.

------------------------------------------------------------------------

## 23. Documentation

Create a complete README containing:

-   Business problem
-   Architecture
-   Tool-calling workflow
-   Azure OpenAI setup
-   Environment variables
-   Database schema
-   KRI configuration
-   Test step interpretation
-   Supported tools
-   Execution plan
-   API documentation
-   Seed instructions
-   Example KRI creation request
-   Example audit run request
-   Example response
-   Person B integration guide
-   Known limitations
-   Security considerations

Include at least one complete example showing:

1.  Azure OpenAI tool call
2.  Tool input
3.  Tool execution
4.  Tool output
5.  Follow-up model call
6.  Final audit result

------------------------------------------------------------------------

## 24. Implementation Stages

### Stage 1: Inspect and plan

-   Inspect the existing project directory.
-   Identify reusable components.
-   Explain the proposed architecture briefly.
-   Do not overwrite existing files without checking them.

### Stage 2: Foundation

Implement:

-   FastAPI
-   SQLite
-   SQLAlchemy
-   Configuration
-   Database initialization
-   Logging

### Stage 3: KRI configuration

Implement:

-   KRI models
-   CRUD APIs
-   Data sources
-   Test steps
-   Thresholds
-   Schedule configuration
-   Review configuration

### Stage 4: Tool framework

Implement:

-   Tool registry
-   Tool schemas
-   Tool handlers
-   Execution context
-   Tool validation

### Stage 5: Azure OpenAI integration

Implement:

-   Provider abstraction
-   Azure OpenAI client
-   Function-calling loop
-   Tool-call parsing
-   Error handling

Use mocked model responses for tests.

### Stage 6: Audit engine

Implement:

-   Deterministic matching
-   Difference calculations
-   Threshold evaluation
-   Metrics
-   Evidence generation
-   Explanation generation

### Stage 7: APIs

Implement:

-   Audit execution API
-   Audit retrieval API
-   Exception API
-   Evidence API
-   Trace API

### Stage 8: Testing and documentation

-   Run unit tests.
-   Run integration tests.
-   Fix errors.
-   Add seed scripts.
-   Complete README.
-   Document known limitations.

------------------------------------------------------------------------

## 25. Definition of Done

The implementation is complete when:

1.  FastAPI starts successfully.
2.  SQLite initializes with multiple tables.
3.  KRI configuration supports all required fields.
4.  Multiple administrator-defined test steps can be created and
    ordered.
5.  A structured execution plan can be generated and validated.
6.  Azure OpenAI function calling invokes registered tools.
7.  Tool arguments are validated.
8.  Tools execute deterministic financial operations.
9.  At least 80 mock transactions are available.
10. The Order Intake vs PO KRI runs successfully.
11. Metrics are calculated from actual records.
12. Exceptions are persisted.
13. Evidence is available for every exception.
14. Tool invocations are traceable.
15. Person B can integrate through documented APIs.
16. Unit and integration tests pass.
17. README documentation is complete.
18. No frontend is implemented.

------------------------------------------------------------------------

## 26. Important Architectural Rules

-   The LLM is an orchestration layer, not the source of truth for
    financial calculations.
-   Tools perform deterministic operations.
-   The database layer controls data access.
-   The audit engine validates and persists final results.
-   Every tool call must be validated and traceable.
-   Every exception must have structured evidence.
-   Do not allow the LLM to bypass mandatory audit stages.
-   Do not hardcode expected exception counts.
-   Do not fabricate records, calculations, or evidence.
-   Prefer a working, testable POC over unnecessary architectural
    complexity.

Start by inspecting the existing project directory and provide a short
implementation plan before making significant changes.
