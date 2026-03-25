Rebuild Assistant Module Proposal
Overview

This document proposes a new module called rebuild_assistant, an AI-assisted legacy system reconstruction tool designed to run on top of the existing Mellow-Link runtime engine.

The goal of this module is to:

Analyze legacy software artifacts (code, database schema, UI templates, configuration)

Understand their structure and implicit architecture

Propose a modernized system design

Generate recomposition drafts for parts of the system

This module does not attempt full automatic system migration.
Instead, it acts as a rebuild planning and draft generation assistant.

Position in System Architecture

The current system already provides a runtime platform capable of executing AI tasks with lifecycle control.

Existing architecture:

Mellow-Link Runtime Engine
│
├ run lifecycle
├ retry / abort
├ progress normalization
├ llm execution
└ modules
    ├ research_assistant
    └ sql_analytics

The new module will be added as:

modules
├ research_assistant
├ sql_analytics
└ rebuild_assistant

The runtime remains unchanged.

rebuild_assistant must conform to the same module interface pattern used by existing modules.

Purpose

The purpose of rebuild_assistant is to help developers modernize legacy systems by:

analyzing legacy structures

identifying architectural problems

proposing a modern architecture

generating initial code/structure drafts

Example use cases:

JSP → React + API modernization

monolithic service → layered architecture

legacy SQL schema → normalized design

tightly coupled UI/DB code → separated layers

Initial Target Domain

The first target environment is RCA-style legacy business systems.

These systems typically have:

tightly coupled UI + SQL logic

outdated frameworks

undocumented business rules

duplicated logic

legacy database schemas

The module should assist in understanding and rebuilding such systems.

V0 Scope (Strictly Limited)

To prevent scope explosion, V0 supports only:

Supported

single feature / single page reconstruction

legacy code structure analysis

architecture modernization proposal

draft generation for new structure

explanation of transformation reasoning

Not Supported

full system automatic migration

multi-service architecture generation

large-scale project refactoring

full data migration automation

guaranteed runnable production code

The output is a structured reconstruction draft, not a complete replacement system.

Input

The module accepts the following input types:

Required

legacy code snippet or file

reconstruction goal

Optional

database schema

SQL queries

UI templates

framework information

constraints

Example input:

{
  "goal": "modernize this JSP-based feature into a React + REST API architecture",
  "assets": {
    "source_code": "...legacy JSP or Java code...",
    "database_schema": "...optional SQL...",
    "ui_template": "...optional..."
  },
  "constraints": [
    "existing business logic must remain unchanged",
    "database entities should remain compatible"
  ]
}
Output

The module produces a structured result with the following format:

One-line conclusion

Legacy system analysis summary

Rebuild strategy

Layer-level reconstruction plan
- database
- backend
- frontend

Recomposition draft
- example SQL
- example API structure
- example UI component

Risks and considerations

Example output:

One-line conclusion
This JSP-based feature should be reconstructed into a React frontend with a REST API backend.

Legacy system analysis summary
- SQL queries embedded directly in UI
- mixed presentation and business logic
- duplicated conditional logic

Rebuild strategy
- separate UI and backend
- extract database access layer
- normalize query responsibilities

Layer-level reconstruction plan
DB: split tables for posts/comments
API: endpoints for list/detail/create/update
UI: React component hierarchy

Recomposition draft
(example SQL / API / UI snippets)

Risks
- authentication flow not fully visible
- file upload logic requires additional analysis
Result Format Standard

To align with other modules (research_assistant, sql_analytics), the output format should follow:

one_line_conclusion
analysis_summary
rebuild_strategy
layer_reconstruction
recomposition_draft
risks

This keeps the user-facing UX consistent across modules.

Failure Handling

The module should not attempt unsafe reconstruction when context is insufficient.

Return a partial result or request more input when:

legacy assets are incomplete

reconstruction goal is too broad

code context is insufficient

architecture inference confidence is low

Example response:

The provided assets are insufficient to determine a reliable reconstruction strategy.
Please provide additional source files or reduce the scope to a single feature.
Module Components

The module follows the existing module structure.

modules/rebuild_assistant
├ adapter.py
├ runner.py
├ service.py
└ tests/
adapter

Responsibilities:

normalize input

parse assets and goal

validate minimum requirements

prepare run metadata

runner

Responsibilities:

manage lifecycle

run analysis step

run reconstruction planning step

run draft generation step

support retry / abort

store result

Runner must follow the same lifecycle pattern used in existing modules.

service

Responsibilities:

legacy code analysis

architecture inference

modernization strategy generation

recomposition draft generation

result formatting

tests

Initial tests should include:

legacy JSP page analysis

legacy SQL schema analysis

architecture reconstruction suggestion

insufficient input handling

result format validation

Execution Flow

The module follows a multi-stage reasoning flow.

Legacy Asset Input
    ↓
Structure Analysis
    ↓
Architecture Reconstruction Planning
    ↓
Recomposition Draft Generation
    ↓
Structured Output
Future Extensions

After V0 is validated, the module may be extended to support:

multi-feature reconstruction

schema reasoning

automated API generation

migration planning

code patch generation

These features are not part of V0.

Key Design Principle

This module is not an automatic code migration engine.

Instead it is:

an AI-assisted legacy system reconstruction tool

It helps developers:

understand legacy systems

plan modernization

generate reconstruction drafts

Summary

rebuild_assistant introduces a new capability to the platform:

Legacy Understanding
→ Architecture Reconstruction
→ Draft Generation

This module complements existing modules and expands the platform into a legacy system modernization assistant.

If implemented correctly, it becomes the first purpose-driven module demonstrating the value of the runtime platform.