"""
Workflow registry — 200 workflow definitions organized by persona segment.
Each workflow has an id, target persona(s), an initial prompt, and 10 evaluation
steps that the testing agent should observe and verify.
"""

import json
import os
import random
from typing import Optional


WORKFLOWS: list[dict] = [

    # ── STUDENT / LEARNER (ST-1) ───────────────────────────────────────────
    {
        "id": "WF-ST-001",
        "personas": ["ST-1"],
        "title": "Study Planner App",
        "initial_prompt": "Build me a study planner app where I can add subjects, set deadlines, and track my progress with a simple dashboard.",
        "expected_agents": ["AIA", "AGP", "App Studio"],
        "steps": [
            "Agent lands on SAI landing page and submits initial prompt",
            "Psi opens and asks clarifying question about preferred tech stack or mobile vs web",
            "Agent responds in ST-1 persona voice (simple, no preference stated)",
            "Flow View appears showing planned steps for study planner build",
            "AIA tab opens on Canvas, agent navigates to AIA and reads workflow graph",
            "Agent checks AIA Overview sub-tab for solution architecture summary",
            "Agent checks AIA Output sub-tab for generated workflow details",
            "App Studio tab appears, agent navigates and reads code generation progress",
            "Agent checks App Studio Thinking sub-tab for adversarial agent review",
            "Agent takes screenshot of final App Studio Output showing ready solution",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask 1-2 clarifying questions about web vs mobile and features needed",
            "output": "Final solution should include a study subject list, deadline tracker, progress bar per subject, and simple dashboard",
        },
    },
    {
        "id": "WF-ST-002",
        "personas": ["ST-1"],
        "title": "Personal Budget Tracker",
        "initial_prompt": "Create a personal budget tracker where I can log income and expenses and see charts of where my money goes.",
        "expected_agents": ["AIA", "ETL", "App Studio"],
        "steps": [
            "Agent submits initial prompt on SAI landing page",
            "Psi asks about data sources: manual entry or bank import?",
            "Agent responds in ST-1 voice: manual entry is fine",
            "Flow View shown with steps for budget tracker build",
            "AIA tab appears on Canvas, agent reads workflow graph",
            "ETL tab appears, agent checks ETL Overview for data mapping plan",
            "ETL Output sub-tab checked for schema and data model",
            "App Studio opens, agent monitors code generation",
            "Agent checks App Studio Questions sub-tab for any pending questions",
            "Agent screenshots final solution — dashboard with pie/bar charts",
        ],
        "evaluation_criteria": {
            "process": "Psi should confirm manual entry vs import and ask about categories to track",
            "output": "Solution should include income/expense logging form, category breakdown charts, and monthly summary",
        },
    },
    {
        "id": "WF-ST-003",
        "personas": ["ST-1"],
        "title": "Flashcard Study App",
        "initial_prompt": "I want a flashcard app to help me memorize vocabulary for my language class. I should be able to create decks and test myself.",
        "expected_agents": ["AIA", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about language and any spaced repetition features needed",
            "Agent responds simply: just basic flip cards with score tracking",
            "Flow View displayed with flashcard app build plan",
            "AIA tab opens, agent navigates and reads workflow structure",
            "AIA Output sub-tab checked for app component breakdown",
            "App Studio opens, agent reads code generation for card flip logic",
            "App Studio Thinking sub-tab checked — adversarial agent reviewing UX",
            "App Studio Output sub-tab checked for final code files",
            "Agent screenshots completed flashcard app solution",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about subject/language, number of cards, and whether spaced repetition is needed",
            "output": "App should include deck creation, card flip interaction, score tracking, and at least two sample decks",
        },
    },

    # ── ACADEMIC RESEARCHER (AC-1) ─────────────────────────────────────────
    {
        "id": "WF-AC-001",
        "personas": ["AC-1"],
        "title": "Research Survey Platform",
        "initial_prompt": "Build a research survey platform with randomized question ordering, consent forms, and CSV export compatible with SPSS.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about IRB compliance requirements and data anonymization needs",
            "Agent responds in AC-1 voice: needs IRB-compliant consent, anonymous responses",
            "Flow View shows all planned agents and sequence",
            "AIA tab opens with survey platform workflow graph",
            "AGP tab opens, agent checks governance rules for data privacy compliance",
            "AGP Output sub-tab checked for privacy and consent policy rules",
            "ETL tab opens for data schema and CSV export mapping",
            "App Studio opens, agent monitors form builder and export code",
            "Agent screenshots final platform showing survey builder and export options",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about IRB consent, anonymization, and export format",
            "output": "Platform should include consent form, randomized questions, response collection, and SPSS-compatible CSV export",
        },
    },
    {
        "id": "WF-AC-002",
        "personas": ["AC-1"],
        "title": "Literature Review Tool",
        "initial_prompt": "Create a tool that takes a list of paper abstracts and generates a structured literature review with themes and research gaps identified.",
        "expected_agents": ["AIA", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about citation format (APA/MLA) and output format (PDF/Word)",
            "Agent responds in AC-1 voice: APA, Word document output",
            "Flow View displayed with literature review tool plan",
            "AIA tab shows NLP pipeline for abstract analysis workflow",
            "AIA Thinking sub-tab checked for agent reasoning on theme extraction",
            "AIA Output sub-tab checked for proposed architecture",
            "App Studio opens with text processing and document generation code",
            "App Studio Questions sub-tab checked for pending input questions",
            "Agent screenshots final output showing structured literature review example",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about citation format, number of papers, and desired output format",
            "output": "Tool should parse abstracts, identify 3+ themes, list gaps, and generate formatted literature review document",
        },
    },

    # ── INDIE BUILDER (IB-1) ───────────────────────────────────────────────
    {
        "id": "WF-IB-001",
        "personas": ["IB-1"],
        "title": "Freelancer Contract Tracker SaaS",
        "initial_prompt": "Build a niche SaaS tool for freelancers to track client contracts with auto-reminders, Stripe billing, and a dashboard. I want to list it on Adya Marketplace.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about target user count, pricing tiers, and Marketplace requirements",
            "Agent responds in IB-1 voice: solo freelancers, $9/mo and $29/mo tiers",
            "Flow View shows full SaaS build pipeline including billing and marketplace config",
            "AIA tab opens with multi-agent workflow graph for SaaS build",
            "AGP tab opens, agent checks governance rules for billing and data ownership",
            "ETL tab shows data schema for contracts, clients, and invoices",
            "App Studio opens, agent monitors Stripe integration and auth code generation",
            "App Studio Thinking sub-tab checked — adversarial agent reviewing security of billing flow",
            "Agent screenshots final solution showing contract dashboard with Stripe billing active",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about pricing tiers, Marketplace listing requirements, and auth method",
            "output": "Solution should include contract CRUD, auto-reminder system, Stripe billing integration, and Marketplace-ready manifest",
        },
    },
    {
        "id": "WF-IB-002",
        "personas": ["IB-1"],
        "title": "Waitlist App with Viral Referral",
        "initial_prompt": "Build a waitlist app with a viral referral loop, email capture, position reveal, and an admin panel to manage signups.",
        "expected_agents": ["AIA", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about referral reward mechanism and email provider",
            "Agent responds: position bump on referral, using Resend for email",
            "Flow View displayed with referral loop architecture",
            "AIA tab shows referral tracking and position calculation workflow",
            "AIA Output sub-tab checked for referral logic design",
            "App Studio opens with landing page and referral code generation code",
            "App Studio Thinking shows adversarial review of referral abuse prevention",
            "App Studio Output shows final files for frontend + API",
            "Agent screenshots admin panel showing waitlist management interface",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about referral reward type, email provider, and admin access control",
            "output": "App should have public waitlist form, unique referral links, position dashboard, email automation, and admin management panel",
        },
    },

    # ── FREELANCER (FR-1) ──────────────────────────────────────────────────
    {
        "id": "WF-FR-001",
        "personas": ["FR-1"],
        "title": "White-Label E-Commerce Dashboard",
        "initial_prompt": "Build a white-label e-commerce dashboard with sales, inventory, and customer metrics that I can reskin per client.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about data sources: Shopify, WooCommerce, or custom API?",
            "Agent responds in FR-1 voice: both Shopify and WooCommerce, white-label branding",
            "Flow View shows multi-source ETL and theming architecture",
            "AIA tab shows dashboard workflow with branding configuration",
            "ETL tab opens showing Shopify and WooCommerce connector mappings",
            "ETL Output sub-tab checked for unified data schema design",
            "AGP tab checked for multi-tenant data isolation governance rules",
            "App Studio opens with theming system and dashboard code",
            "Agent screenshots white-label dashboard with example client branding",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about data source types, theming requirements, and multi-tenant isolation needs",
            "output": "Dashboard should support Shopify + WooCommerce, have configurable logo/color branding, and per-client data isolation",
        },
    },

    # ── SMALL AGENCY (AG-1) ────────────────────────────────────────────────
    {
        "id": "WF-AG-001",
        "personas": ["AG-1"],
        "title": "Agency Project Management Dashboard",
        "initial_prompt": "Build a project management dashboard for our agency tracking all client projects, team utilization, deadlines, and billing status.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about team size, existing tools (Asana/Jira?), and billing system",
            "Agent responds in AG-1 voice: 15 people, using Asana, billing via QuickBooks",
            "Flow View shows integrations and dashboard architecture plan",
            "AIA tab opens with project tracking workflow graph",
            "ETL tab shows Asana and QuickBooks data connector mapping",
            "ETL Output checked for unified project+billing schema",
            "AGP tab checked for client data access isolation governance",
            "App Studio opens with dashboard and Gantt-style timeline code",
            "Agent screenshots final project management dashboard with utilization view",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about team size, existing PM tools, and billing system integration",
            "output": "Dashboard should show active projects per client, team utilization heatmap, deadline calendar, and invoice status",
        },
    },

    # ── CITIZEN DEVELOPER (CD-1) ───────────────────────────────────────────
    {
        "id": "WF-CD-001",
        "personas": ["CD-1"],
        "title": "Warehouse Barcode Intake Form",
        "initial_prompt": "Build a mobile-friendly warehouse intake form that scans barcodes and logs items into our inventory automatically.",
        "expected_agents": ["AIA", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about existing inventory system and barcode format (QR/1D)",
            "Agent responds in CD-1 voice: using Excel as inventory, standard 1D barcodes",
            "Flow View shows mobile form + Excel sync architecture",
            "AIA tab shows form workflow and barcode integration design",
            "ETL tab shows Excel data schema and sync mapping",
            "ETL Questions sub-tab checked for data mapping clarifications",
            "App Studio opens with mobile form and camera barcode scanner code",
            "App Studio Output shows final PWA-ready form files",
            "Agent screenshots mobile form with barcode scan interface",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about existing inventory system, barcode type, and offline capability needs",
            "output": "Solution should be mobile-optimized PWA, support 1D barcode scanning via camera, sync to Excel/Google Sheets, and work offline",
        },
    },
    {
        "id": "WF-CD-002",
        "personas": ["CD-1"],
        "title": "SLA Monitoring & Alert System",
        "initial_prompt": "Build an SLA monitoring dashboard that shows shipment status and sends Slack alerts when a delivery is at risk of breaching SLA.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about carrier APIs and SLA thresholds per delivery type",
            "Agent responds: using FedEx and UPS APIs, SLA is 48h standard, 24h express",
            "Flow View shows carrier polling + alert pipeline architecture",
            "AIA tab shows SLA calculation workflow with risk scoring",
            "ETL tab shows FedEx/UPS API data mapping and normalization",
            "AGP tab shows alert escalation governance rules",
            "AGP Output sub-tab checked for notification policy configuration",
            "App Studio opens with monitoring dashboard and Slack webhook code",
            "Agent screenshots dashboard with at-risk shipments highlighted in red",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about carrier APIs, SLA thresholds, and alert recipients",
            "output": "Dashboard should show all active shipments with SLA risk score, color coding, and automated Slack alerts for at-risk shipments",
        },
    },

    # ── STARTUP FOUNDER (FN-1) ────────────────────────────────────────────
    {
        "id": "WF-FN-001",
        "personas": ["FN-1"],
        "title": "Investor Reporting Dashboard",
        "initial_prompt": "Build an investor reporting dashboard showing MRR, runway, top customer logos, and key metrics — clean and professional.",
        "expected_agents": ["AIA", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about data sources for MRR (Stripe?) and runway calculation",
            "Agent responds in FN-1 voice: Stripe for revenue, manual runway input",
            "Flow View shows dashboard architecture with Stripe integration",
            "AIA tab shows investor dashboard workflow with metric calculations",
            "ETL tab shows Stripe data connector and MRR/churn calculation schema",
            "ETL Output sub-tab checked for financial metric definitions",
            "App Studio opens with dashboard styling — clean, investor-grade design",
            "App Studio Thinking shows adversarial agent checking data accuracy",
            "Agent screenshots final investor dashboard with all KPI tiles",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about revenue data source, what metrics matter to investors, and update frequency",
            "output": "Dashboard should show MRR with MoM growth, runway in months, top 10 customers, ARR, churn rate, and DAU",
        },
    },
    {
        "id": "WF-FN-002",
        "personas": ["FN-1"],
        "title": "Lead Qualification Workflow",
        "initial_prompt": "Create a lead scoring system that auto-qualifies inbound leads from our website contact form based on company size and intent signals.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about scoring criteria and CRM integration (HubSpot/Salesforce?)",
            "Agent responds: company size + job title + page views, using HubSpot",
            "Flow View shows lead ingestion → scoring → CRM update pipeline",
            "AIA tab shows lead scoring workflow with decision logic",
            "AGP tab shows governance rules for data handling and CRM write access",
            "ETL tab shows HubSpot API connector and lead data schema",
            "App Studio opens with scoring algorithm and HubSpot integration code",
            "App Studio Thinking shows adversarial review of scoring logic edge cases",
            "Agent screenshots admin view of scored leads with qualification tier",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about scoring dimensions, CRM system, and routing rules for qualified leads",
            "output": "System should score leads A/B/C based on company size + title + intent, push to HubSpot with score, and trigger Slack notification for A leads",
        },
    },

    # ── SENIOR ENGINEER (SE-1) ────────────────────────────────────────────
    {
        "id": "WF-SE-001",
        "personas": ["SE-1"],
        "title": "Kafka Real-Time Streaming Pipeline",
        "initial_prompt": "Build a Kafka-based real-time pipeline with exactly-once semantics ingesting clickstream events and a consumer lag monitoring dashboard.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about expected event volume, topic partitioning strategy, and downstream sink",
            "Agent responds in SE-1 voice: 50K events/sec, 12 partitions, sink to ClickHouse",
            "Flow View shows streaming architecture with Kafka, ClickHouse, and monitoring",
            "AIA tab shows streaming workflow graph with producer/consumer topology",
            "ETL tab shows event schema, transformation logic, and ClickHouse sink mapping",
            "ETL Thinking sub-tab checked for deduplication strategy discussion",
            "AGP tab shows data retention and exactly-once transaction governance rules",
            "App Studio opens with Kafka consumer group and lag monitoring dashboard code",
            "Agent screenshots consumer lag dashboard with partition-level metrics",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about event volume, partitioning, consumer group design, and downstream storage",
            "output": "Solution should include Kafka producer config, idempotent consumer with exactly-once semantics, ClickHouse sink, and lag dashboard per partition",
        },
    },
    {
        "id": "WF-SE-002",
        "personas": ["SE-1"],
        "title": "dbt Data Transformation Layer",
        "initial_prompt": "Build a dbt-based incremental transformation layer replacing our Informatica pipelines with full test coverage and documentation.",
        "expected_agents": ["AIA", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about source database, number of existing pipelines, and target warehouse",
            "Agent responds: Postgres source, 45 pipelines, Snowflake target",
            "Flow View shows dbt project structure and migration plan",
            "AIA tab shows transformation workflow with model dependency graph",
            "ETL tab shows source-to-target column mapping with transformation rules",
            "ETL Output sub-tab checked for dbt model definitions",
            "App Studio opens with dbt project scaffolding, models, and test files",
            "App Studio Thinking shows adversarial review of incremental strategy edge cases",
            "Agent screenshots dbt project output with DAG visualization and test results",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about source/target systems, pipeline count, incremental strategy preference, and testing standards",
            "output": "Output should be a complete dbt project with incremental models, schema tests, source freshness tests, and auto-generated documentation",
        },
    },

    # ── TECHNICAL PM (PM-1) ───────────────────────────────────────────────
    {
        "id": "WF-PM-001",
        "personas": ["PM-1"],
        "title": "SaaS Analytics Dashboard",
        "initial_prompt": "Build a real-time SaaS analytics dashboard with Stripe revenue, DAU, MRR, and churn metrics for my product team.",
        "expected_agents": ["AIA", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about data refresh rate and team access controls",
            "Agent responds in PM-1 voice: real-time for revenue, daily for engagement; team-level access",
            "Flow View shows dashboard pipeline with Stripe and product DB connections",
            "AIA tab shows metric calculation workflow for MRR/churn/DAU",
            "ETL tab shows Stripe and product database connector schemas",
            "ETL Output checked for metric definition alignment (MRR = monthly_revenue calculation)",
            "App Studio opens with dashboard component and charting library code",
            "App Studio Thinking shows adversarial review of metric accuracy",
            "Agent screenshots final dashboard with all four KPI tiles and trend charts",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about metric definitions (how to calculate churn), data sources, and access control requirements",
            "output": "Dashboard should show Stripe MRR with trend, DAU with 7/30-day trend, churn rate, and ARR — all with time-period filters",
        },
    },
    {
        "id": "WF-PM-002",
        "personas": ["PM-1"],
        "title": "Customer Journey Funnel Tracker",
        "initial_prompt": "Build a customer journey tracking app showing funnel drop-off by cohort with A/B test result overlays.",
        "expected_agents": ["AIA", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about event tracking source and cohort definition",
            "Agent responds: Mixpanel as source, cohorts by signup week",
            "Flow View shows funnel analysis pipeline with Mixpanel integration",
            "AIA tab shows funnel workflow with cohort segmentation logic",
            "ETL tab shows Mixpanel API connector and event schema mapping",
            "ETL Thinking sub-tab checked for cohort calculation methodology",
            "App Studio opens with funnel visualization and A/B test overlay code",
            "App Studio Output sub-tab checked for final component files",
            "Agent screenshots funnel chart with cohort comparison and A/B overlay",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about funnel steps, cohort grouping, and A/B test identification method",
            "output": "App should show conversion funnel per cohort, drop-off rates per step, statistical significance for A/B tests, and date range filters",
        },
    },

    # ── AI/ML ENGINEER (AI-1) ─────────────────────────────────────────────
    {
        "id": "WF-AI-001",
        "personas": ["AI-1"],
        "title": "ML Model Monitoring System",
        "initial_prompt": "Build an ML model monitoring system with Prometheus metrics, KL-divergence drift detection, and automated Slack alerts on model degradation.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about model type, inference volume, and drift threshold definition",
            "Agent responds in AI-1 voice: classification model, 10K predictions/day, drift threshold p-value < 0.05",
            "Flow View shows monitoring pipeline with Prometheus, drift detector, and alert manager",
            "AIA tab shows monitoring workflow with statistical test selection",
            "AGP tab shows governance rules for model versioning and alert escalation policy",
            "ETL tab shows prediction log schema and feature distribution tracking",
            "App Studio opens with Prometheus exporter, KL-divergence detector, and Slack webhook code",
            "App Studio Thinking shows adversarial review of drift detection statistical correctness",
            "Agent screenshots monitoring dashboard with feature drift heatmap and alert log",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about model type, feature count, drift detection method preference, and alerting threshold",
            "output": "System should export Prometheus metrics, compute KL divergence per feature, trigger Slack alert when threshold exceeded, and show Grafana-compatible dashboard config",
        },
    },
    {
        "id": "WF-AI-002",
        "personas": ["AI-1"],
        "title": "RAG Clinical Q&A System",
        "initial_prompt": "Deploy a RAG system over our clinical documentation corpus using vector embeddings, LangChain, and a GPT-4 backbone.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about document format, PHI handling requirements, and latency SLA",
            "Agent responds: PDFs, HIPAA compliant, sub-3 second response",
            "Flow View shows RAG pipeline: ingest → embed → retrieve → generate",
            "AIA tab shows full RAG architecture workflow with retrieval strategy",
            "AGP tab shows HIPAA data handling and access control governance rules",
            "AGP Output checked for PHI redaction and audit trail policies",
            "ETL tab shows PDF ingestion, chunking, and vector store schema",
            "App Studio opens with LangChain RAG chain, pgvector config, and API endpoint code",
            "Agent screenshots RAG system with example clinical query and cited source output",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about PHI handling, document volume, embedding model, and vector store preference",
            "output": "System should include PDF ingestion pipeline, OpenAI embeddings, pgvector storage, LangChain retrieval chain, FastAPI endpoint, and cited source references in answers",
        },
    },

    # ── SOLUTION ARCHITECT (SA-1) ─────────────────────────────────────────
    {
        "id": "WF-SA-001",
        "personas": ["SA-1"],
        "title": "Internal Developer Portal",
        "initial_prompt": "Build an internal developer portal with service catalog, runbooks, deployment status, and on-call schedules integrated with PagerDuty.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about existing service registry, team count, and SSO requirements",
            "Agent responds in SA-1 voice: no registry yet, 12 teams, Okta SSO required",
            "Flow View shows portal architecture with Okta, PagerDuty, and GitHub integrations",
            "AIA tab shows developer portal workflow with service catalog structure",
            "AGP tab shows access control governance — who can edit vs view per service",
            "ETL tab shows PagerDuty, GitHub Actions, and Okta data connector schemas",
            "ETL Output checked for service metadata normalization schema",
            "App Studio opens with portal frontend, service catalog API, and SSO integration code",
            "Agent screenshots portal showing service catalog with health status and on-call schedule",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about team structure, SSO provider, existing tooling (GitHub/GitLab), and deployment tracking method",
            "output": "Portal should include service catalog with owner/SLO/links, runbook viewer, live deployment status from CI/CD, PagerDuty on-call schedule, and Okta SSO",
        },
    },
    {
        "id": "WF-SA-002",
        "personas": ["SA-1"],
        "title": "AI-Powered Code Review Pipeline",
        "initial_prompt": "Create an AI-powered code review system that checks PRs for security vulnerabilities, performance anti-patterns, and coding standards violations.",
        "expected_agents": ["AIA", "AGP", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about language stack, Git provider (GitHub/GitLab), and severity thresholds",
            "Agent responds: Python + TypeScript, GitHub, block merge on critical findings",
            "Flow View shows PR analysis pipeline with GitHub webhooks and AI review agents",
            "AIA tab shows code review workflow with parallel security + perf + style agents",
            "AGP tab shows code review policy governance — severity definitions and merge rules",
            "AGP Output checked for merge blocking policy configuration",
            "App Studio opens with GitHub Actions workflow, AI reviewer agent, and PR comment bot code",
            "App Studio Thinking shows adversarial review of false positive rate handling",
            "Agent screenshots example PR review with inline comments and summary report",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about language stack, Git provider, severity classification, and merge gate configuration",
            "output": "System should include GitHub Actions trigger, parallel AI analysis for security/perf/style, inline PR comments, merge-blocking for critical issues, and weekly report",
        },
    },

    # ── DEVOPS ENGINEER (DO-1) ────────────────────────────────────────────
    {
        "id": "WF-DO-001",
        "personas": ["DO-1"],
        "title": "Kubernetes Observability Platform",
        "initial_prompt": "Build a Kubernetes observability platform with Prometheus metrics, pod health, resource utilization, and SLO tracking per service.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about cluster count, Kubernetes version, and existing Grafana setup",
            "Agent responds in DO-1 voice: 3 clusters, k8s 1.29, no existing Grafana",
            "Flow View shows observability stack architecture with Prometheus + Grafana + Alertmanager",
            "AIA tab shows monitoring pipeline workflow with metrics collection strategy",
            "ETL tab shows Kubernetes metrics API and Prometheus scrape config schema",
            "AGP tab shows SLO definition governance and alert escalation policy",
            "AGP Output checked for SLO budget burn rate alert rules",
            "App Studio opens with Helm chart, Prometheus config, Grafana dashboard JSON, and Alertmanager rules",
            "Agent screenshots Grafana-like dashboard showing cluster health and SLO burn rates",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about cluster count, Kubernetes distro, existing observability stack, and SLO definitions",
            "output": "Solution should include Prometheus Helm chart, custom service SLO dashboards, pod restart/OOM alerts, CPU/memory utilization per namespace, and Alertmanager routing rules",
        },
    },
    {
        "id": "WF-DO-002",
        "personas": ["DO-1"],
        "title": "Terraform Drift Detection System",
        "initial_prompt": "Create a Terraform drift detection system that runs every 30 minutes and sends Slack alerts when infrastructure diverges from desired state.",
        "expected_agents": ["AIA", "AGP", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about cloud provider, number of Terraform workspaces, and state backend",
            "Agent responds: AWS, 24 workspaces, S3 state backend with DynamoDB locking",
            "Flow View shows drift detection pipeline with scheduled runs and alerting",
            "AIA tab shows drift detection workflow with workspace iteration strategy",
            "AGP tab shows drift response governance — what level requires immediate action vs logging",
            "App Studio opens with Python scheduler, terraform plan runner, and Slack alert code",
            "App Studio Thinking shows adversarial review of race condition in concurrent plan runs",
            "App Studio Output shows final Lambda + EventBridge deployment package",
            "Agent screenshots drift alert example showing changed resources with plan diff",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about cloud provider, workspace count, state backend, and acceptable drift response time",
            "output": "System should use EventBridge cron, iterate all workspaces, run terraform plan, parse changes, send Slack alert with resource diff, and log to DynamoDB",
        },
    },

    # ── ENTERPRISE ARCHITECT (EA-1) ────────────────────────────────────────
    {
        "id": "WF-EA-001",
        "personas": ["EA-1"],
        "title": "Data Governance Platform",
        "initial_prompt": "Build a cloud-native data governance platform replacing our mainframe reporting, SOX compliant with full audit logging and data lineage.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about mainframe data format, regulatory scope, and number of business units",
            "Agent responds in EA-1 voice: COBOL flat files, SOX + GDPR scope, 12 BUs",
            "Flow View shows governance platform architecture with all four agents engaged",
            "AIA tab shows governance workflow with policy enforcement and reporting pipeline",
            "AGP tab opens — primary agent for this workflow — governance rule definitions",
            "AGP Output sub-tab checked for SOX control mappings and GDPR data classification rules",
            "ETL tab shows mainframe COBOL file parser and transformation to cloud schema",
            "App Studio opens with governance portal, audit trail, and lineage graph code",
            "Agent screenshots governance dashboard with data lineage graph and compliance status",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about regulatory frameworks, data classification levels, audit retention period, and BU access isolation",
            "output": "Platform should include COBOL file parser, data catalog with classification, SOX control mapping, GDPR consent tracking, automated audit reports, and data lineage visualization",
        },
    },
    {
        "id": "WF-EA-002",
        "personas": ["EA-1"],
        "title": "Enterprise API Gateway",
        "initial_prompt": "Create an API gateway with rate limiting, mTLS authentication, and centralized logging for our 47 internal microservices.",
        "expected_agents": ["AIA", "AGP", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about expected API volume, service mesh existing setup, and logging target",
            "Agent responds: 50K req/min peak, no service mesh, logs to Splunk",
            "Flow View shows API gateway architecture with mTLS, rate limiting, and Splunk sink",
            "AIA tab shows gateway workflow with routing, auth, and rate limiting components",
            "AGP tab shows API security governance — mTLS certificate policies and rate limit tiers",
            "AGP Output checked for rate limiting tier definitions and certificate rotation policy",
            "App Studio opens with Kong/custom gateway config, mTLS setup, and Splunk forwarder code",
            "App Studio Thinking shows adversarial review of rate limit bypass edge cases",
            "Agent screenshots gateway admin console showing service registry and traffic metrics",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about API volume, authentication method, service discovery mechanism, and logging destination",
            "output": "Gateway should include mTLS certificate management, per-service rate limiting, centralized request/response logging to Splunk, health check endpoints, and admin console",
        },
    },

    # ── BUSINESS LEADER (BL-1) ────────────────────────────────────────────
    {
        "id": "WF-BL-001",
        "personas": ["BL-1"],
        "title": "Executive Financial Dashboard",
        "initial_prompt": "I need a live financial dashboard showing our cash position, monthly burn rate, and runway — connect it to our bank and NetSuite.",
        "expected_agents": ["AIA", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about update frequency and who will have access to this dashboard",
            "Agent responds in BL-1 voice: updates daily, CFO + board members only",
            "Flow View shows finance dashboard architecture with bank and NetSuite connectors",
            "AIA tab shows dashboard workflow with financial calculation logic",
            "ETL tab shows NetSuite and bank API connector schemas and authentication",
            "ETL Output checked for cash position and burn rate calculation definitions",
            "App Studio opens with executive dashboard — clean, presentation-grade design",
            "App Studio Thinking shows adversarial review of financial calculation accuracy",
            "Agent screenshots final executive dashboard with cash, burn, and runway tiles",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about data sources, access control, update frequency, and what metrics board specifically wants",
            "output": "Dashboard should show real-time cash balance, 30/60/90-day burn rate, runway in months, MoM variance, and be accessible only to CFO + board with SSO",
        },
    },
    {
        "id": "WF-BL-002",
        "personas": ["BL-1"],
        "title": "Automated Month-End Close System",
        "initial_prompt": "Build me an automated month-end close system with reconciliation checklists and a final P&L report sent to the board automatically.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about current close process steps and accounting system",
            "Agent responds: using NetSuite, 12 reconciliation steps, board meeting is 3rd Tuesday",
            "Flow View shows close automation pipeline with approval workflow",
            "AIA tab shows close workflow with task sequencing and approval gates",
            "AGP tab shows financial governance — approval authority matrix and audit trail rules",
            "ETL tab shows NetSuite transaction data connector and reconciliation schema",
            "App Studio opens with checklist application, approval workflow, and PDF report generator",
            "App Studio Output checked for final report template and email scheduler",
            "Agent screenshots month-end dashboard showing checklist progress and auto-send schedule",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about accounting system, number of reconciliation steps, approval chain, and board report delivery method",
            "output": "System should include digital reconciliation checklist with sign-offs, automated NetSuite data pull, P&L PDF generator, and scheduled email to board on close date",
        },
    },

    # ── GSI PARTNER (IP-1) ────────────────────────────────────────────────
    {
        "id": "WF-IP-001",
        "personas": ["IP-1"],
        "title": "Enterprise Data Governance Accelerator",
        "initial_prompt": "Build a reusable enterprise data governance accelerator that I can deploy for financial services clients in under 3 days.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about target regulatory framework, client tech stack assumptions, and reuse mechanism",
            "Agent responds in IP-1 voice: SOX + GDPR, Azure + SQL Server assumed, template-based reuse",
            "Flow View shows accelerator architecture with parameterized template design",
            "AIA tab shows governance accelerator workflow with client customization hooks",
            "AGP tab shows pre-built governance rule templates for SOX and GDPR",
            "AGP Output checked for reusable policy template library",
            "ETL tab shows SQL Server connector with parameterized schema mapping",
            "App Studio opens with template engine, config-driven governance portal, and deployment scripts",
            "Agent screenshots accelerator template gallery with one-click deployment options",
        ],
        "evaluation_criteria": {
            "process": "Psi should ask about target regulatory frameworks, common client tech stacks, customization depth, and Marketplace publishing requirements",
            "output": "Accelerator should be template-driven with config files for SOX/GDPR rules, parameterized ETL connectors, one-command deployment to Azure, and Adya Marketplace manifest",
        },
    },
    {
        "id": "WF-IP-002",
        "personas": ["IP-1"],
        "title": "Multi-Client Deployment Dashboard",
        "initial_prompt": "Create a multi-tenant client management dashboard where my team can monitor all active Adya deployments across 12 enterprise clients.",
        "expected_agents": ["AIA", "AGP", "ETL", "App Studio"],
        "steps": [
            "Agent submits prompt on landing page",
            "Psi asks about access control model and what metrics matter most across client deployments",
            "Agent responds: team members see all clients, clients see only their own; track uptime, usage, and billing",
            "Flow View shows multi-tenant dashboard architecture with Adya API integration",
            "AIA tab shows multi-client monitoring workflow with data isolation design",
            "AGP tab shows tenant isolation governance — data boundaries and access rules",
            "AGP Output checked for role definitions: partner-admin vs client-viewer",
            "ETL tab shows Adya platform API connector for deployment health metrics",
            "App Studio opens with multi-tenant portal, client switcher, and billing summary code",
            "Agent screenshots partner dashboard showing all 12 clients with status indicators",
        ],
        "evaluation_criteria": {
            "process": "Psi must ask about access hierarchy, data isolation requirements, and what deployment health metrics to surface",
            "output": "Dashboard should show all client deployments with health status, usage metrics, billing summaries, role-based access (partner vs client views), and alert configuration per client",
        },
    },
]


def get_all_workflows() -> list[dict]:
    return WORKFLOWS


def get_workflows_for_persona(persona_id: str) -> list[dict]:
    return [w for w in WORKFLOWS if persona_id in w["personas"]]


def get_workflow_by_id(workflow_id: str) -> Optional[dict]:
    for w in WORKFLOWS:
        if w["id"] == workflow_id:
            return w
    return None


def get_random_workflow(persona_id: Optional[str] = None) -> dict:
    pool = get_workflows_for_persona(persona_id) if persona_id else WORKFLOWS
    if not pool:
        pool = WORKFLOWS
    return random.choice(pool)


def load_persona(persona_id: str) -> Optional[dict]:
    """
    Load a persona by ID. YAML files (BRD F1 schema) take precedence over JSON.
    Falls back to legacy JSON format transparently.
    """
    from app.personas.persona_schema import normalise_persona, validate_persona

    personas_dir = os.path.join(os.path.dirname(__file__), "..", "personas")

    # Try YAML first (BRD F1 preferred format)
    try:
        import yaml
        for ext in (".yaml", ".yml"):
            yaml_path = os.path.join(personas_dir, f"{persona_id}{ext}")
            if os.path.exists(yaml_path):
                with open(yaml_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return normalise_persona(data)
    except Exception:
        pass

    # Fallback to JSON
    path = os.path.join(personas_dir, f"{persona_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return normalise_persona(data)
