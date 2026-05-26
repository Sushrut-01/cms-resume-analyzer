# =============================================================================
# services/llm_service.py — Core AI Logic (Resume Analysis & Rewriting)
#
# This is the most important service file. It handles all communication with
# the AI provider and all resume processing logic.
#
# Key responsibilities:
#
#   1. _call_llm(prompt) — unified AI caller
#      Routes to whichever provider is configured (Ollama/Azure/Groq/Gemini/NVIDIA).
#      All AI calls go through this single function.
#
#   2. _strip_contact_pii(text) — PII removal (CRITICAL for compliance)
#      Called before EVERY AI request. Removes:
#        - Email addresses → [email]
#        - Phone numbers   → [phone]
#        - LinkedIn URLs   → [linkedin]
#        - GitHub URLs     → [github]
#        - Street addresses → [address]
#        - Candidate name (first capitalised line) → [name]
#      This ensures no personal identifiable information is sent to external AI.
#
#   3. analyze_resume(resume_text, jd_text) — main analysis function
#      - Strips PII from resume text
#      - Scores the resume against the JD (keyword match + semantic score)
#      - Identifies gaps, strengths, must-have missing, nice-to-have missing
#      - Generates an AI-rewritten improved resume
#      - Returns all results as a structured dict
#
#   4. generate_aligned_resume(resume_text, jd_text) — JD alignment
#      - Strips PII from resume text
#      - Rewrites the resume to better match the JD
#      - Adds missing skills and rephrases existing bullets
#      - Returns before/after scores and alignment history
#
#   5. check_ollama_status() / get_provider_status() — health checks
#      Used by the UI to show whether the AI is online/offline.
#
# Outbound network calls:
#   - Ollama: localhost only (no internet)
#   - Azure OpenAI: azure_endpoint from ai_config.json (Kforce's own Azure)
#   - Groq: api.groq.com (external cloud, sends prompt text only — no raw resume PII after strip)
#   - Gemini: generativelanguage.googleapis.com (external cloud, same PII strip applies)
#   - NVIDIA NIM: integrate.api.nvidia.com (external cloud, same PII strip applies)
# =============================================================================

import requests
import os
import re
import sys
import json

# Add parent dir so we can import ai_config when running from services/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_config

# Semantic service — imported lazily so missing packages don't crash the app
try:
    from services.semantic_service import detect_soft_gaps, semantic_score as _semantic_score, get_tier as _sem_tier
    _SEMANTIC_AVAILABLE = True
except ImportError:
    _SEMANTIC_AVAILABLE = False
    def detect_soft_gaps(*a, **kw):
        return {"available": False, "tier": "none", "soft_gaps": [], "true_gaps": kw.get("hard_gap_kws", a[1] if len(a) > 1 else []), "section_matches": {}}
    def _semantic_score(*a, **kw): return None
    def _sem_tier(): return "none"

DIVIDER = "━" * 35


# ── Unified LLM caller ────────────────────────────────────────────────────────

# -----------------------------------------------------------------------------
# _call_llm — Unified AI Caller (single entry point for ALL AI calls)
#
# Every AI request in the entire application goes through this function.
# It reads the active provider from ai_config.json and routes to the right API.
# This means switching AI providers only requires changing one setting — no code change.
#
# IMPORTANT: All prompts passed here should already have PII stripped by
# _strip_contact_pii() before calling this function.
# -----------------------------------------------------------------------------
def _call_llm(prompt: str, max_tokens: int = 200) -> str:
    """Call whichever provider is active. Raises RuntimeError with message on API failure."""
    cfg      = ai_config.load()
    provider = cfg.get("provider", "ollama")
    # Route to the correct provider implementation
    if provider == "gemini":
        return _gemini(prompt, max_tokens, cfg)
    if provider == "azure":
        return _azure_openai(prompt, max_tokens, cfg)   # Kforce production provider
    if provider == "groq":
        return _groq(prompt, max_tokens, cfg)
    if provider == "nvidia":
        return _nvidia_nim(prompt, max_tokens, cfg)
    return _ollama(prompt, max_tokens, cfg)              # default: local Ollama


def _ollama(prompt: str, max_tokens: int = 200, cfg: dict = None) -> str:
    cfg = cfg or ai_config.load()
    url   = cfg.get("ollama_url", "http://localhost:11434")
    model = cfg.get("ollama_model", "qwen2.5:0.5b")
    try:
        payload = {
            "model":   model,
            "prompt":  prompt,
            "stream":  False,
            "options": {
                "temperature":   0.1,   # lower = more deterministic, less hallucination
                "num_predict":   max_tokens,
                "repeat_penalty": 1.2,  # discourage repetitive filler text
                "top_p":         0.9,
                "stop":          ["\n\n", "Note:", "Example:", "Here is"],
            },
        }
        r = requests.post(f"{url}/api/generate", json=payload, timeout=120)
        if r.status_code == 200:
            return r.json().get("response", "").strip()
    except Exception:
        pass
    return ""


def _azure_openai(prompt: str, max_tokens: int = 200, cfg: dict = None) -> str:
    cfg        = cfg or ai_config.load()
    endpoint   = cfg.get("azure_endpoint", "").rstrip("/")
    api_key    = cfg.get("azure_api_key", "")
    deployment = cfg.get("azure_deployment", "gpt-4o")
    api_ver    = cfg.get("azure_api_version", "2024-08-01-preview")
    if not endpoint or not api_key:
        return ""
    try:
        url     = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_ver}"
        headers = {"api-key": api_key, "Content-Type": "application/json"}
        body    = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        r = requests.post(url, headers=headers, json=body, timeout=30)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return ""


def _groq(prompt: str, max_tokens: int = 200, cfg: dict = None) -> str:
    import time as _time
    cfg     = cfg or ai_config.load()
    api_key = cfg.get("groq_api_key", "")
    model   = cfg.get("groq_model", "meta-llama/llama-4-scout-17b-16e-instruct")
    if not api_key:
        return ""
    url     = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body    = {
        "model":       model,
        "messages":    [{"role": "user", "content": prompt}],
        "max_tokens":  max_tokens,
        "temperature": 0.3,
    }
    for attempt in range(2):
        try:
            # Llama 4 / large extraction prompts may take slightly longer — 45s timeout
            r = requests.post(url, headers=headers, json=body, timeout=45)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            if r.status_code == 429 and attempt == 0:
                # Respect Retry-After header; default to 10 s if absent
                wait = int(r.headers.get("Retry-After", 10))
                _time.sleep(min(wait, 30))  # cap at 30 s so we don't block forever
                continue
            err = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
            raise RuntimeError(f"Groq error: {err}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Groq request failed: {e}")
    raise RuntimeError("Groq rate limit: retry exhausted")


def _nvidia_nim(prompt: str, max_tokens: int = 200, cfg: dict = None) -> str:
    cfg     = cfg or ai_config.load()
    api_key = cfg.get("nvidia_api_key", "")
    model   = cfg.get("nvidia_model", "meta/llama-3.1-70b-instruct")
    if not api_key:
        return ""
    try:
        url     = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body    = {
            "model":       model,
            "messages":    [{"role": "user", "content": prompt}],
            "max_tokens":  max_tokens,
            "temperature": 0.3,
        }
        r = requests.post(url, headers=headers, json=body, timeout=30)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        err = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
        raise RuntimeError(f"NVIDIA NIM error: {err}")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"NVIDIA NIM request failed: {e}")


def _gemini(prompt: str, max_tokens: int = 200, cfg: dict = None) -> str:
    cfg     = cfg or ai_config.load()
    api_key = cfg.get("gemini_api_key", "")
    model   = cfg.get("gemini_model", "gemini-1.5-flash")
    if not api_key:
        return ""
    try:
        url  = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3},
        }
        r = requests.post(url, json=body, timeout=30)
        if r.status_code == 200:
            candidates = r.json().get("candidates", [])
            if candidates:
                return candidates[0]["content"]["parts"][0]["text"].strip()
        # Raise descriptive error so callers can surface it
        err = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
        raise RuntimeError(f"Gemini error: {err}")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Gemini request failed: {e}")

# Minimal grammar-only filter — ONLY true linguistic function words.
# Domain words (testing, monitoring, automation, compliance, etc.) are intentionally
# excluded so they contribute to scoring for ANY job domain without code changes.
_GRAMMAR_WORDS = {
    # Articles
    'a', 'an', 'the',
    # Core prepositions
    'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as', 'into',
    'onto', 'over', 'under', 'above', 'below', 'between', 'among', 'through',
    'during', 'before', 'after', 'within', 'without', 'across', 'along',
    'around', 'behind', 'beside', 'beyond', 'despite', 'except', 'near',
    'since', 'until', 'toward', 'towards', 'about', 'against', 'off', 'out',
    'up', 'down', 'per', 'via', 'upon', 'like', 'than', 're',
    # Conjunctions
    'and', 'or', 'but', 'nor', 'so', 'yet', 'although', 'because', 'unless',
    'while', 'though', 'if', 'when', 'where', 'whether', 'however', 'therefore',
    'furthermore', 'moreover', 'nevertheless', 'otherwise', 'hence', 'thus',
    'also', 'either', 'neither', 'both',
    # Auxiliary / modal verbs
    'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
    'do', 'does', 'did', 'will', 'would', 'shall', 'should', 'could', 'may',
    'might', 'must', 'can',
    # Pronouns / determiners
    'i', 'me', 'my', 'we', 'us', 'our', 'you', 'your', 'he', 'him', 'his',
    'she', 'her', 'it', 'its', 'they', 'them', 'their', 'who', 'whom', 'whose',
    'what', 'which', 'this', 'that', 'these', 'those',
    # Quantifiers / degree words
    'all', 'any', 'each', 'every', 'few', 'more', 'most', 'other', 'some',
    'such', 'no', 'not', 'only', 'same', 'very', 'just', 'even', 'well',
    # Universal hiring boilerplate — appears identically in ALL JDs/resumes
    # These are NOT domain-specific; removing them is purely structural.
    'etc',
    # Time / quantity
    'years', 'year',
    # Hiring vocabulary (not role vocabulary)
    'candidate', 'candidates', 'team', 'teams',
    'role', 'roles', 'position', 'positions', 'job', 'jobs',
    'required', 'requirement', 'requirements',
    'preferred', 'desired', 'ideal', 'minimum',
    'responsibility', 'responsibilities',
    # Requirement qualifier words (signal importance but are NOT skills themselves)
    'essential', 'mandatory', 'critical', 'minimum', 'preferred', 'desired',
    # Adjective filler
    'strong', 'excellent', 'good', 'proven', 'demonstrated',
    'able', 'nice', 'fast', 'new', 'high', 'low', 'key',
    # Generic proficiency adjectives
    'automated', 'advanced', 'basic', 'intermediate', 'expertise',
    # Generic noun filler
    'experience', 'experienced',
    'skill', 'skills',
    'knowledge',
    'ability', 'abilities',
    'understanding',
    'familiar', 'familiarity',
    'proficient', 'proficiency',
    # Generic verb filler
    'use', 'using', 'used',
    'work', 'working', 'worked',
    'need', 'needs',
    'seeking', 'looking',
    'including', 'following',
    'responsible', 'ensure', 'provide',
}

# Recognised technology domains — keywords here are guaranteed real tech
_CLOUD    = {'aws','azure','gcp','kubernetes','docker','terraform','eks','ecs',
             'lambda','s3','ec2','rds','cloudformation','helm','ansible','argocd',
             'istio','openshift','cloudtrail','cloudwatch','dynamodb','sqs','sns',
             'kinesis','route53','cloudfront','waf','iam','ecr','aurora','redshift',
             'athena','glue','elasticache','fargate','stepfunctions','eventbridge',
             'apigw','cognito','amplify','lightsail','beanstalk','opsworks',
             'gcprun','bigquery','pubsub','gke','dataflow','vertexai','firestore',
             'azureml','azuredevops','keyvault','appservice','aks','azurefunctions',
             # Amazon Connect & chat services
             'amazonconnect','connect','startchatcontact','participantservice',
             'contactflow','routingprofile','lexbot','polly','transcribe',
             # Security / identity tokens — treated as tech for injection
             'jwt','saml','oauth','sso','azuread','mfa','scp','vpcflowlogs',
             'hipaa','soc2','gdpr','pii','phi','encryptionatrest',
             # Messaging / real-time
             'websocket','webhook','grpc','signalr','pusher','socket'}
_BACKEND  = {'java','python','nodejs','springboot','spring','fastapi','flask',
             'django','rest','api','microservices','golang','scala','kotlin',
             'grpc','graphql','rabbitmq','kafka','celery','gunicorn','uvicorn',
             'jersey','jaxrs','quarkus','vertx','micronaut','nestjs','expressjs',
             'hibernate','jpa','mybatis','liquibase','flyway','rxjava','reactor'}
_DATA     = {'sql','mysql','postgresql','mongodb','redis','elasticsearch','spark',
             'hadoop','hive','airflow','dbt','snowflake','bigquery','etl',
             'databricks','cassandra','neo4j','influxdb','timescaledb','clickhouse',
             'presto','trino','iceberg','delta','pandas','numpy','pyspark',
             'streamlit','superset','tableau','powerbi','looker','metabase',
             'pinecone','weaviate','chroma','milvus','qdrant','faiss'}
_AI       = {'tensorflow','pytorch','scikit','keras','xgboost','lightgbm',
             'huggingface','transformers','openai','langchain','llamaindex',
             'llm','rag','embeddings','vectordb','mlflow','kubeflow','sagemaker',
             'azureml','vertexai','promptengineering','finetuning','lora',
             'opencv','nltk','spacy','bert','gpt','llama','mistral','gemini',
             'anthropic','claude','pinecone','weaviate','chroma','milvus','faiss'}
_FRONTEND = {'react','angular','vue','javascript','typescript','html','css',
             'nextjs','nuxtjs','svelte','redux','mobx','webpack','vite','babel',
             'tailwind','bootstrap','materialui','antdesign','flutter',
             'reactnative','ionic','electron','jquery','sass','less','storybook'}
_DEVOPS   = {'cicd','jenkins','github','gitlab','bitbucket','sonarqube',
             'prometheus','grafana','splunk','datadog','newrelic','jira',
             'confluence','nexus','artifactory','vault','consul','pagerduty',
             'opsgenie','statuspage','nagios','zabbix','dynatrace','elk',
             'logstash','kibana','fluentd','jaeger','zipkin','opentelemetry',
             'githubactions','circleci','travisci','teamcity','bamboo','argo',
             # DevOps activity keywords
             'monitoring','logging','alerting','observability','security',
             'devops','sre','oncall','incident','deployment','release',
             'pipeline','automation','infrastructure','containerization'}
_TESTING  = {'junit','pytest','selenium','cypress','jest','testng','mockito',
             'postman','swagger','cucumber','karate','gatling','jmeter',
             'locust','playwright','webdriver','appium','detox','jasmine',
             'mocha','chai','sinon','supertest','pact','wiremock','testcontainers',
             # QA activity keywords — real skills for testing roles
             'testing','automation','manual','regression','functional',
             'integration','performance','load','stress','api','sanity',
             'smoke','exploratory','uiautomation','testautomation',
             'qualityassurance','bugtracking','defect','testcase','testplan',
             'testscript','testexecution','testmanagement'}
_CRM      = {'salesforce','hubspot','dynamics','zoho','sap','oracle','servicenow',
             'zendesk','freshdesk','marketo','pardot'}

_ALL_TECH = _CLOUD | _BACKEND | _DATA | _AI | _FRONTEND | _DEVOPS | _TESTING | _CRM

# ── Domain definitions — drives coverage dashboard + per-JD detection ─────────
# injection_supported = True  → system can inject aligned keywords into resume
# injection_supported = False → analysis + gap detection work, but no injection
# Adding a new domain here is enough to update the dashboard automatically.
_DOMAIN_DEFINITIONS = [
    # ── Fully supported (analysis + injection) ──
    {"name": "Backend & APIs",       "injection_supported": True,
     "indicators": {"java","python","nodejs","spring","fastapi","flask","django",
                    "rest","api","microservices","golang","kotlin","grpc","graphql",
                    "rabbitmq","kafka","hibernate"}},
    {"name": "Cloud & Infrastructure","injection_supported": True,
     "indicators": {"aws","azure","gcp","kubernetes","docker","terraform","helm",
                    "ansible","eks","ecs","lambda","cloudformation","argocd","istio"}},
    {"name": "AI & Machine Learning", "injection_supported": True,
     "indicators": {"tensorflow","pytorch","scikit","keras","llm","openai","langchain",
                    "mlflow","sagemaker","vertexai","embeddings","rag","huggingface",
                    "xgboost","lightgbm","nlp","transformers"}},
    {"name": "Data & Databases",      "injection_supported": True,
     "indicators": {"sql","postgresql","mongodb","redis","elasticsearch","spark",
                    "hadoop","airflow","snowflake","databricks","etl","dbt",
                    "bigquery","tableau","powerbi","pandas","pyspark"}},
    {"name": "Frontend",              "injection_supported": True,
     "indicators": {"react","angular","vue","javascript","typescript","html","css",
                    "nextjs","redux","webpack","tailwind","bootstrap","svelte",
                    "materialui","figma"}},
    {"name": "CI/CD & DevSecOps",     "injection_supported": True,
     "indicators": {"jenkins","github","gitlab","cicd","sonarqube","prometheus",
                    "grafana","datadog","splunk","monitoring","alerting","observability",
                    "githubactions","circleci","vault","argocd"}},
    {"name": "Testing & QA",          "injection_supported": True,
     "indicators": {"selenium","cypress","testng","junit","pytest","automation",
                    "testing","regression","playwright","manual","appium","jmeter",
                    "postman","cucumber","mockito","qualityassurance",
                    "usability","accessibility","uat","defect","testcase","testplan",
                    "bugtracking","functional","performance","sanity","smoke"}},
    {"name": "CRM & ERP",             "injection_supported": True,
     "indicators": {"salesforce","sap","oracle","servicenow","dynamics","hubspot",
                    "zoho","zendesk","freshdesk","marketo","pardot"}},
    # ── Analysis-only (scoring + gap detection work; no injection yet) ─────────
    {"name": "Finance & Accounting",  "injection_supported": False,
     "indicators": {"financial","accounting","budgeting","forecasting","auditing",
                    "gaap","ifrs","cpa","cfa","modeling","reconciliation","ledger",
                    "revenue","cost","portfolio","treasury","valuation"}},
    {"name": "Human Resources",       "injection_supported": False,
     "indicators": {"recruitment","hiring","onboarding","payroll","hrms","hris",
                    "talent","workforce","compensation","benefits","appraisal",
                    "headcount","attrition","sourcing","interviewing"}},
    {"name": "Legal & Compliance",    "injection_supported": False,
     "indicators": {"legal","compliance","regulatory","contract","litigation",
                    "gdpr","hipaa","sox","law","attorney","counsel","governance",
                    "privacy","policy","jurisdiction","statute"}},
    {"name": "Marketing & Sales",     "injection_supported": False,
     "indicators": {"marketing","sales","campaign","seo","sem","analytics",
                    "conversion","leads","revenue","branding","digital","funnel",
                    "crm","roi","gtm","adwords","social","content"}},
    {"name": "Healthcare & Medical",  "injection_supported": False,
     "indicators": {"clinical","medical","patient","hospital","healthcare","ehr",
                    "hipaa","nursing","pharmaceutical","diagnosis","treatment",
                    "radiology","pathology","surgical","icu","emr"}},
    {"name": "Operations & Supply Chain","injection_supported": False,
     "indicators": {"logistics","supply","inventory","procurement","warehouse",
                    "manufacturing","lean","sigma","erp","vendor","shipping",
                    "distribution","forecasting","production","quality"}},
    {"name": "Design & UX",           "injection_supported": False,
     "indicators": {"ux","ui","wireframe","prototype","figma","sketch","adobe",
                    "photoshop","illustrator","userresearch","heuristic",
                    "persona","journey","interaction","visual","typography",
                    "designsystem","mockup","userflow","information"}},
    {"name": "Project Management",    "injection_supported": False,
     "indicators": {"agile","scrum","kanban","pmp","prince2","waterfall","sprint",
                    "milestone","roadmap","stakeholder","deliverable","risk",
                    "change","program","portfolio","governance","charter"}},
]


# ─────────────────────────────────────────────────────────────────────────────
# JOB FUNCTION DETECTION & ROLE COMPATIBILITY
# ─────────────────────────────────────────────────────────────────────────────

_JOB_FUNCTIONS = [
    {"name": "QA / Testing",
     "indicators": {"selenium","cypress","testng","junit","pytest","playwright","appium",
                    "jmeter","cucumber","testing","automation","regression","defect","qa",
                    "qualityassurance","testplan","bugtracking","manual","smoke","sanity",
                    "functional","performance","load","uat","accessibility","usability",
                    "testrail","zephyr","qtest","test"}},
    {"name": "Backend Development",
     "indicators": {"java","spring","springboot","fastapi","flask","django","nodejs","express",
                    "rest","api","microservices","golang","kotlin","grpc","graphql","kafka",
                    "rabbitmq","backend","hibernate","mvc","dotnet","csharp","php","ruby",
                    "nestjs","quarkus","vertx"}},
    {"name": "Frontend Development",
     "indicators": {"react","angular","vue","javascript","typescript","html","css","nextjs",
                    "redux","webpack","svelte","tailwind","bootstrap","frontend","dom","jsx",
                    "tsx","sass","nuxt","gatsby","storybook"}},
    {"name": "Full Stack Development",
     "indicators": {"fullstack","mern","mean","mevn","react","angular","nodejs","backend",
                    "frontend","restapi","mongodb","postgres","mysql"}},
    {"name": "DevOps / Infrastructure",
     "indicators": {"kubernetes","docker","terraform","ansible","jenkins","cicd","helm",
                    "argocd","prometheus","grafana","devops","infrastructure","cloudformation",
                    "pipeline","deployment","monitoring","sre","gitops","vault","istio",
                    "githubactions","circleci","datadog","splunk"}},
    {"name": "Data Science / AI / ML",
     "indicators": {"tensorflow","pytorch","scikit","keras","llm","openai","langchain",
                    "mlflow","embeddings","rag","huggingface","nlp","transformers","xgboost",
                    "lightgbm","datascience","machinelearning","deeplearning","neuralnetwork",
                    "classification","regression","clustering","pandas","numpy"}},
    {"name": "Data Engineering",
     "indicators": {"spark","hadoop","airflow","snowflake","databricks","etl","dbt","bigquery",
                    "hive","pyspark","dataengineering","warehouse","ingestion","flink","nifi",
                    "glue","redshift","fivetran","datalake","kafka"}},
    {"name": "Mobile Development",
     "indicators": {"android","ios","swift","reactnative","flutter","xamarin","xcode",
                    "mobile","objective","playstore","appstore","kotlin","swiftui",
                    "jetpack","coroutines"}},
    {"name": "Prompt Engineering / GenAI",
     "indicators": {"prompt","promptengineering","llm","langchain","openai","agentic","rag",
                    "chatbot","generativeai","genai","gpt","claude","gemini","copilot",
                    "aiagent","vectordb","finetuning","lora","chainofthought","multiagent",
                    "mcp","aiassistant","conversational","chatflow","knowledgebase"}},
    {"name": "Security / Cybersecurity",
     "indicators": {"penetration","owasp","vulnerability","siem","soc","firewall","ids",
                    "encryption","cissp","ceh","security","ethical","hacking","threat",
                    "incident","forensics","vapt","zerotrust","dlp","pentest"}},
    {"name": "Solution Architecture",
     "indicators": {"architect","architecture","solution","hld","lld","enterprise",
                    "integration","patterns","scalability","resilience","technical",
                    "design","distributed","eventdriven","cqrs","saga"}},
    {"name": "Business Analysis",
     "indicators": {"requirements","stakeholder","brd","frd","uml","usecase","usersto",
                    "businessanalysis","gapanalysis","processmap","workflow","wireframe",
                    "functional","specification","traceability","epics"}},
    {"name": "Project Management",
     "indicators": {"pmp","prince2","scrummaster","agile","kanban","roadmap","milestone",
                    "projectmanagement","program","portfolio","governance","charter",
                    "delivery","risk","change","sprint"}},
    {"name": "CRM / ERP / Salesforce",
     "indicators": {"salesforce","sap","oracle","servicenow","dynamics","crm","erp","apex",
                    "soql","lwc","visualforce","flow","hubspot","zoho","freshdesk"}},
    {"name": "Cloud Engineering",
     "indicators": {"aws","azure","gcp","cloudformation","lambda","ec2","s3","rds","eks",
                    "aks","gke","cloudwatch","iam","vpc","route53","cdn","waf","cloudtrail"}},
]

# Compatibility matrix — only define one direction; lookup is symmetric.
# "compatible" = strong fit, "adjacent" = transferable but gaps expected,
# "incompatible" = fundamentally different role → warn recruiter
_ROLE_COMPAT = {
    ("QA / Testing",             "QA / Testing"):               "compatible",
    ("QA / Testing",             "DevOps / Infrastructure"):    "adjacent",
    ("QA / Testing",             "Backend Development"):        "adjacent",
    ("QA / Testing",             "Frontend Development"):       "adjacent",
    ("QA / Testing",             "Prompt Engineering / GenAI"): "adjacent",
    ("QA / Testing",             "Security / Cybersecurity"):   "adjacent",
    ("QA / Testing",             "Solution Architecture"):      "adjacent",
    ("Backend Development",      "Backend Development"):        "compatible",
    ("Backend Development",      "Full Stack Development"):     "compatible",
    ("Backend Development",      "Frontend Development"):       "adjacent",
    ("Backend Development",      "Data Science / AI / ML"):     "adjacent",
    ("Backend Development",      "Prompt Engineering / GenAI"): "adjacent",
    ("Backend Development",      "DevOps / Infrastructure"):    "adjacent",
    ("Backend Development",      "Solution Architecture"):      "adjacent",
    ("Backend Development",      "CRM / ERP / Salesforce"):     "adjacent",
    ("Backend Development",      "Cloud Engineering"):          "adjacent",
    ("Frontend Development",     "Frontend Development"):       "compatible",
    ("Frontend Development",     "Full Stack Development"):     "compatible",
    ("Frontend Development",     "Backend Development"):        "adjacent",
    ("Full Stack Development",   "Full Stack Development"):     "compatible",
    ("Full Stack Development",   "Backend Development"):        "compatible",
    ("Full Stack Development",   "Frontend Development"):       "compatible",
    ("Full Stack Development",   "Solution Architecture"):      "adjacent",
    ("Data Science / AI / ML",   "Data Science / AI / ML"):    "compatible",
    ("Data Science / AI / ML",   "Prompt Engineering / GenAI"):"compatible",
    ("Data Science / AI / ML",   "Data Engineering"):           "adjacent",
    ("Data Science / AI / ML",   "Backend Development"):        "adjacent",
    ("Data Engineering",         "Data Engineering"):           "compatible",
    ("Data Engineering",         "Data Science / AI / ML"):     "adjacent",
    ("Data Engineering",         "Backend Development"):        "adjacent",
    ("Data Engineering",         "DevOps / Infrastructure"):    "adjacent",
    ("Data Engineering",         "Cloud Engineering"):          "adjacent",
    ("DevOps / Infrastructure",  "DevOps / Infrastructure"):   "compatible",
    ("DevOps / Infrastructure",  "Cloud Engineering"):         "compatible",
    ("DevOps / Infrastructure",  "Backend Development"):       "adjacent",
    ("DevOps / Infrastructure",  "Security / Cybersecurity"):  "adjacent",
    ("DevOps / Infrastructure",  "Solution Architecture"):     "adjacent",
    ("Mobile Development",       "Mobile Development"):        "compatible",
    ("Mobile Development",       "Frontend Development"):      "adjacent",
    ("Mobile Development",       "Full Stack Development"):    "adjacent",
    ("Prompt Engineering / GenAI","Prompt Engineering / GenAI"):"compatible",
    ("Prompt Engineering / GenAI","Data Science / AI / ML"):   "compatible",
    ("Prompt Engineering / GenAI","Backend Development"):      "adjacent",
    ("Security / Cybersecurity", "Security / Cybersecurity"):  "compatible",
    ("Security / Cybersecurity", "DevOps / Infrastructure"):   "adjacent",
    ("Security / Cybersecurity", "Cloud Engineering"):         "adjacent",
    ("Solution Architecture",    "Solution Architecture"):     "compatible",
    ("Solution Architecture",    "Backend Development"):       "adjacent",
    ("Solution Architecture",    "Full Stack Development"):    "adjacent",
    ("Solution Architecture",    "Cloud Engineering"):         "adjacent",
    ("Business Analysis",        "Business Analysis"):         "compatible",
    ("Business Analysis",        "Project Management"):        "adjacent",
    ("Business Analysis",        "CRM / ERP / Salesforce"):    "adjacent",
    ("Project Management",       "Project Management"):        "compatible",
    ("Project Management",       "Business Analysis"):         "adjacent",
    ("CRM / ERP / Salesforce",   "CRM / ERP / Salesforce"):   "compatible",
    ("CRM / ERP / Salesforce",   "Backend Development"):       "adjacent",
    ("CRM / ERP / Salesforce",   "Business Analysis"):         "adjacent",
    ("Cloud Engineering",        "Cloud Engineering"):         "compatible",
    ("Cloud Engineering",        "DevOps / Infrastructure"):   "compatible",
    ("Cloud Engineering",        "Backend Development"):       "adjacent",
    ("Cloud Engineering",        "Solution Architecture"):     "adjacent",
}


def _expand_text_for_matching(text: str) -> tuple:
    """Return (normal_lower, compressed) versions of text for indicator matching.
    Compressed removes all whitespace so compound indicators like 'generativeai'
    match JD text like 'Generative AI' or 'GitHub Actions' → 'githubactions'.
    Short indicators (≤4 chars) only use normal matching to avoid false positives."""
    t = text.lower()
    t_compressed = re.sub(r'\s+', '', t)
    return t, t_compressed


def _count_indicator_hits(indicators: set, t_normal: str, t_compressed: str) -> int:
    """Count how many indicators match the text.
    Long indicators (≥5 chars) also checked against whitespace-compressed text.
    SBERT used as a final soft-match layer when the model is loaded."""
    exact = sum(1 for kw in indicators if kw in t_normal)
    compressed_bonus = sum(
        1 for kw in indicators
        if len(kw) >= 5 and kw not in t_normal and kw in t_compressed
    )
    return exact + compressed_bonus


def detect_function(text: str) -> str:
    """Detect the primary job function from resume or JD text.
    Returns the function name string, or 'Unknown'.
    Uses both exact and whitespace-compressed matching so 'Generative AI'
    matches the 'generativeai' indicator and 'GitHub Actions' matches 'githubactions'."""
    t_normal, t_compressed = _expand_text_for_matching(text)
    scored = []
    for fn in _JOB_FUNCTIONS:
        hits = _count_indicator_hits(fn["indicators"], t_normal, t_compressed)
        if hits > 0:
            scored.append((hits, fn["name"]))
    if not scored:
        return "Unknown"
    scored.sort(reverse=True)
    return scored[0][1]


def check_role_compatibility(candidate_fn: str, jd_fn: str) -> dict:
    """Check compatibility between candidate function and JD function.
    Returns verdict: compatible | adjacent | incompatible | unknown"""
    if candidate_fn == "Unknown" or jd_fn == "Unknown":
        return {
            "verdict": "unknown",
            "candidate_function": candidate_fn,
            "jd_function": jd_fn,
            "message": "Could not determine role from resume or JD text.",
            "color": "grey",
        }
    key     = (candidate_fn, jd_fn)
    rev_key = (jd_fn, candidate_fn)
    verdict = _ROLE_COMPAT.get(key) or _ROLE_COMPAT.get(rev_key)
    if verdict is None:
        verdict = "incompatible"
    msgs = {
        "compatible":   f"Strong role match — {candidate_fn} aligns directly with {jd_fn}.",
        "adjacent":     f"Partial match — candidate is {candidate_fn}, JD targets {jd_fn}. Transferable skills exist but expect significant gaps.",
        "incompatible": f"Role mismatch — candidate is {candidate_fn}, JD requires {jd_fn}. These are fundamentally different roles; alignment results will be misleading.",
    }
    colors = {"compatible": "green", "adjacent": "yellow", "incompatible": "red"}
    return {
        "verdict":            verdict,
        "candidate_function": candidate_fn,
        "jd_function":        jd_fn,
        "message":            msgs[verdict],
        "color":              colors[verdict],
    }


def detect_jd_domain(jd_text: str) -> dict:
    """Detect the most likely job domain from JD text using indicator keyword overlap.
    Purely statistical — no hardcoded rules beyond indicator word lists.
    Returns domain name, injection support flag, and confidence score.

    Priority rule: tech domains (injection_supported=True) beat non-tech domains when
    both have hits. Tech JDs routinely mention compliance/legal/healthcare requirements
    as context (HIPAA, GDPR, SOX) without being those roles — we must not let that
    contextual language hijack domain detection and restrict system capabilities."""
    t_normal, t_compressed = _expand_text_for_matching(jd_text)
    scored = []
    for d in _DOMAIN_DEFINITIONS:
        hits = _count_indicator_hits(d["indicators"], t_normal, t_compressed)
        if hits > 0:
            scored.append((hits, d))
    if not scored:
        return {
            "domain": "General / Unknown",
            "injection_supported": False,
            "confidence": 0,
            "top_matches": [],
        }
    scored.sort(key=lambda x: x[0], reverse=True)
    best_hits, best_domain = scored[0]

    # If the top winner is a non-tech domain but tech domains also have hits,
    # prefer the best tech domain (requires ≥ 2 hits to be considered a real signal).
    if not best_domain["injection_supported"]:
        tech_scored = [(h, d) for h, d in scored if d["injection_supported"] and h >= 2]
        if tech_scored:
            best_hits, best_domain = tech_scored[0]

    confidence = round(best_hits / len(best_domain["indicators"]) * 100)
    return {
        "domain": best_domain["name"],
        "injection_supported": best_domain["injection_supported"],
        "confidence": confidence,
        "top_matches": [
            {"domain": d["name"], "hits": h, "injection_supported": d["injection_supported"]}
            for h, d in scored[:3]
        ],
    }


def get_domain_coverage() -> dict:
    """Return domain coverage summary.
    Reflects the current state of the codebase — no manual updates needed."""
    supported   = [d for d in _DOMAIN_DEFINITIONS if d["injection_supported"]]
    unsupported = [d for d in _DOMAIN_DEFINITIONS if not d["injection_supported"]]
    return {
        "total_domains": len(_DOMAIN_DEFINITIONS),
        "fully_supported": len(supported),
        "analysis_only": len(unsupported),
        "domains": [
            {
                "name": d["name"],
                "injection_supported": d["injection_supported"],
                "status": "Full Support" if d["injection_supported"] else "Analysis Only",
                "indicator_count": len(d["indicators"]),
            }
            for d in _DOMAIN_DEFINITIONS
        ],
    }


# Tech suffix/prefix patterns — conservative to avoid false positives
_TECH_SUFFIX = re.compile(r'(db|sql|ml|api|sdk|cli|gql|mq)$', re.I)
_TECH_PREFIX = re.compile(r'^(aws|azure|gcp|docker|kube|terra|spring|fast|flask|redis|elastic|mongo|kafka|spark|react|angular|node|nest|next|nuxt|svelte|vue|type|java|pyth|scala|kotl|golan|rust)', re.I)

# ── Alias expansion — variants that mean the same tech ───────────────────────
# Key = variant found in JD/resume → Value = canonical form to match against
_TECH_ALIASES: dict[str, str] = {
    # Kubernetes
    "k8s": "kubernetes", "k8": "kubernetes", "kube": "kubernetes",
    # JavaScript ecosystem
    "js": "javascript", "ts": "typescript", "nodejs": "nodejs", "node.js": "nodejs",
    "reactjs": "react", "react.js": "react", "vuejs": "vue", "vue.js": "vue",
    "angularjs": "angular", "nextjs": "nextjs", "nuxtjs": "nuxtjs",
    # Databases
    "postgres": "postgresql", "pg": "postgresql", "psql": "postgresql",
    "mongo": "mongodb", "mssql": "sql", "mysql": "mysql",
    # Cloud
    "gcp": "gcp", "az": "azure", "s3": "s3",
    # CI/CD
    "ci/cd": "cicd", "ci-cd": "cicd", "github actions": "githubactions",
    "gh actions": "githubactions",
    # ML/AI
    "sklearn": "scikit", "scikit-learn": "scikit", "scikit learn": "scikit",
    "tf": "tensorflow", "pt": "pytorch", "hf": "huggingface",
    "nlp": "nltk", "llms": "llm",
    # Spring
    "spring boot": "springboot", "spring-boot": "springboot",
    # Messaging
    "rmq": "rabbitmq", "rabbit": "rabbitmq",
    # Search
    "elastic": "elasticsearch", "elk": "elasticsearch",
    # REST
    "restful": "rest", "rest api": "rest", "restapi": "rest",
    "rest assured test": "rest assured", "rest-assured": "rest assured",
    # Microservices
    "microservice": "microservices", "micro-service": "microservices",
    # Others
    "devops": "cicd", "docker-compose": "docker", "dockerfile": "docker",
    "helm chart": "helm", "iac": "terraform",
    "openai api": "openai", "chat gpt": "gpt", "chatgpt": "gpt",
    # Amazon Connect
    "amazon connect": "amazonconnect", "aws connect": "amazonconnect",
    "connect chat": "amazonconnect", "contact flows": "contactflow",
    # Security / auth
    "azure active directory": "azuread", "active directory": "azuread",
    "json web token": "jwt", "json web tokens": "jwt",
    "saml sso": "saml", "single sign-on": "sso",
    "multi-factor": "mfa", "two factor": "mfa",
    "vpc flow logs": "vpcflowlogs", "cloud trail": "cloudtrail",
    # Real-time
    "web sockets": "websocket", "websockets": "websocket",
}

# Important bigram tech phrases to match as units (not split into words)
_TECH_BIGRAMS = {
    "machine learning", "deep learning", "natural language processing",
    "large language model", "computer vision", "data science",
    "ci cd", "continuous integration", "continuous deployment",
    "spring boot", "node js", "react native", "type script",
    "rest api", "graph ql", "micro services", "api gateway",
    "test driven", "behavior driven", "object oriented",
    "data engineering", "data pipeline", "feature engineering",
    "neural network", "random forest", "gradient boosting",
    "vector database", "retrieval augmented", "prompt engineering",
    # Cloud / AWS services
    "amazon connect", "contact flow", "routing profile", "lex bot",
    "vpc flow", "azure ad", "azure devops", "azure openai",
    # Security / compliance
    "single sign on", "multi factor", "role based", "least privilege",
    "audit log", "audit trail", "identity provider",
    # Real-time / integration
    "web socket", "event driven", "message queue", "service bus",
    "function calling", "structured output", "context window",
    "agentic workflow", "agent handoff", "transfer to agent",
    # SQL / ETL / Data — generic terms used across many domains
    "window function", "window functions", "common table expression",
    "data warehouse", "data mart", "data lake", "semantic layer",
    "etl testing", "data reconciliation", "data validation",
    "null handling", "set operation", "union all",
}


# ── Resume boilerplate stripper ───────────────────────────────────────────────
# Lines matching any of these patterns add zero value to the Word doc output.
_JUNK_PHRASES = [
    r"^i am responsible for following activities\s*:?\s*$",
    r"^i am responsible for following\s*:?\s*$",
    r"^i was responsible for following activities\s*:?\s*$",
    r"^following are (the\s+)?responsibilities\s*:?\s*$",
    r"^key responsibilities\s*:?\s*$",
    r"^job profile\s*:.*",
    r"^roles\s*[&/and]*\s*responsibilities\s*:?\s*$",
    r"^description\s*:?\s*$",
    r"^technologies\s*[&/and]*\s*tools\s*:?\s*$",
    r"^project description\s*:?\s*$",
    r"^academic projects?\s*:?\s*$",
    r"^i hereby declare.*",
    r"^date\s*:?\s*$",
    r"^personal details?\s*:?\s*$",
    r"^date of birth\s*:.*",
    r"^gender\s*:.*",
    r"^marital status\s*:.*",
    r"^address\s*:.*",
    r"^nationality\s*:.*",
    r"^declaration\s*:?\s*$",
    r"^references?\s*:?\s*(available)?\s*$",
    r"^language[s]?\s*known\s*:?\s*$",
    r"^hobbies\s*:?\s*$",
    r"^interests?\s*:?\s*$",
]

# -----------------------------------------------------------------------------
# _strip_contact_pii — PII Removal (CALLED BEFORE EVERY AI REQUEST)
#
# Replaces all personal identifiable information with placeholder tokens.
# This ensures that candidate personal details NEVER reach external AI providers.
#
# What gets replaced:
#   email address   → [email]
#   phone number    → [phone]
#   LinkedIn URL    → [linkedin]
#   GitHub URL      → [github]
#   street address  → [address]
#   candidate name  → [name]  (first capitalised line of resume, top 5 lines)
#
# The AI still receives: skills, experience bullets, education, certifications.
# It does NOT receive: who the person is or how to contact them.
# -----------------------------------------------------------------------------
def _strip_contact_pii(text: str) -> str:
    """Remove name, email, phone, address, and LinkedIn URL before sending to AI."""
    # Replace any email address pattern
    text = re.sub(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b', '[email]', text)
    # Replace phone numbers (handles +91, country codes, spaces, dashes, brackets)
    text = re.sub(r'(\+?\d[\d\s\-().]{7,}\d)', '[phone]', text)
    # Replace LinkedIn profile URLs
    text = re.sub(r'(https?://)?(www\.)?linkedin\.com/in/[^\s,|>]+', '[linkedin]', text, flags=re.I)
    # Replace GitHub profile URLs
    text = re.sub(r'(https?://)?(www\.)?github\.com/[^\s,|>]+', '[github]', text, flags=re.I)
    # Replace street address patterns (house number + street type keyword)
    text = re.sub(r'\b\d{1,5}\s+[A-Za-z0-9\s,.#]{5,40}(?:street|st|avenue|ave|road|rd|blvd|lane|ln|drive|dr|court|ct|way)\b',
                  '[address]', text, flags=re.I)
    # Replace candidate name — looks for Title Case words-only line in the first 5 lines
    # Resume convention: name is always the very first line with no digits or symbols
    lines = text.split('\n')
    for i, line in enumerate(lines[:5]):
        stripped = line.strip()
        if stripped and re.match(r'^[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3}$', stripped):
            lines[i] = '[name]'
            break
    return '\n'.join(lines)


def clean_resume_text(text: str) -> str:
    """Strip boilerplate/junk phrases from resume text before any processing.
    Removes: responsibility headers, personal details, declarations, etc."""
    lines = text.split("\n")
    cleaned = []
    skip_personal = False
    for line in lines:
        s = line.strip()
        lower = s.lower()
        # Stop capturing after personal details block
        if re.match(r'^personal details?\s*:?\s*$', lower, re.I):
            skip_personal = True
            continue
        if skip_personal:
            # Resume after personal block only if real section heading encountered
            if re.match(r'^(academic|education|certif|project|career|experience|skills|summary|professional)', lower, re.I):
                skip_personal = False
            else:
                continue
        # Strip junk phrases
        if s and any(re.match(p, lower, re.I) for p in _JUNK_PHRASES):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _is_tech_keyword(kw: str) -> bool:
    """Return True only if keyword looks like a real technology/tool name."""
    if len(kw) < 3:
        return False
    kl = kw.lower().replace('-', '').replace('_', '').replace('.', '').replace('/', '')
    if kl in _ALL_TECH:
        return True
    if _TECH_PREFIX.match(kl):
        return True
    if _TECH_SUFFIX.search(kl) and len(kw) >= 4:
        return True
    # Version-tagged tech: java11, python3, node18
    if re.match(r'^[a-z]{3,12}\d+$', kl):
        return True
    return False


def _truncate(text: str, max_chars: int) -> str:
    return text[:max_chars] if text else ""


# -----------------------------------------------------------------------------
# check_ollama_status / get_provider_status
# Used by the UI to show the AI online/offline indicator.
# check_ollama_status: legacy name, now checks ALL providers not just Ollama.
# get_provider_status: returns detailed status dict for the settings page.
# -----------------------------------------------------------------------------
def check_ollama_status() -> bool:
    """Legacy check — still used by candidates router. Checks active provider."""
    cfg      = ai_config.load()
    provider = cfg.get("provider", "ollama")
    # Cloud providers: check if API key is configured (can't ping without a request)
    if provider == "gemini":
        return bool(cfg.get("gemini_api_key", ""))
    if provider == "azure":
        return bool(cfg.get("azure_endpoint") and cfg.get("azure_api_key"))
    if provider == "groq":
        return bool(cfg.get("groq_api_key", ""))
    if provider == "nvidia":
        return bool(cfg.get("nvidia_api_key", ""))
    try:
        r = requests.get(f"{cfg.get('ollama_url', 'http://localhost:11434')}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def get_provider_status() -> dict:
    cfg = ai_config.load()
    provider = cfg.get("provider", "ollama")
    if provider == "gemini":
        online = bool(cfg.get("gemini_api_key", ""))
        return {"online": online, "provider": "gemini", "model": cfg.get("gemini_model", "gemini-1.5-flash")}
    if provider == "azure":
        online = bool(cfg.get("azure_endpoint") and cfg.get("azure_api_key"))
        return {"online": online, "provider": "azure", "model": cfg.get("azure_deployment", "gpt-4o")}
    if provider == "groq":
        online = bool(cfg.get("groq_api_key", ""))
        return {"online": online, "provider": "groq", "model": cfg.get("groq_model", "meta-llama/llama-4-scout-17b-16e-instruct")}
    if provider == "nvidia":
        online = bool(cfg.get("nvidia_api_key", ""))
        return {"online": online, "provider": "nvidia", "model": cfg.get("nvidia_model", "meta/llama-3.1-70b-instruct")}
    try:
        r = requests.get(f"{cfg.get('ollama_url', 'http://localhost:11434')}/api/tags", timeout=5)
        online = r.status_code == 200
    except Exception:
        online = False
    return {"online": online, "provider": "ollama", "model": cfg.get("ollama_model", "qwen2.5:0.5b")}


def _normalise(kw: str) -> str:
    """Normalise a keyword to its canonical alias form."""
    kl = kw.lower().strip()
    return _TECH_ALIASES.get(kl, kl)


def _extract_keywords(text: str) -> set:
    """Extract unigrams + important bigrams, normalised through alias table.
    Filters only true grammar words so domain-specific terms always pass through."""
    tl = text.lower()
    words = re.findall(r'\b[A-Za-z][A-Za-z0-9+#.]{1,}\b', text)
    kws = {_normalise(w) for w in words
           if w.lower() not in _GRAMMAR_WORDS and len(w) >= 3}
    # Extract bigrams and check against known tech bigrams
    tokens = re.findall(r'\b[a-z][a-z0-9]{1,}\b', tl)
    for i in range(len(tokens) - 1):
        bigram = f"{tokens[i]} {tokens[i+1]}"
        if bigram in _TECH_BIGRAMS:
            kws.add(bigram.replace(" ", ""))   # store as joined form e.g. "machinelearning"
    return kws


def _score_keywords_by_jd_freq(jd_text: str, keywords: set) -> dict:
    """Score each keyword by how many times it appears in the JD.
    Keywords repeated more = more important to the role."""
    jd_lower = jd_text.lower()
    scores = {}
    for kw in keywords:
        # Check both the keyword and any known aliases
        pattern = r'\b' + re.escape(kw) + r'\b'
        count = len(re.findall(pattern, jd_lower, re.I))
        # Also count original form if normalised
        for alias, canonical in _TECH_ALIASES.items():
            if canonical == kw:
                count += len(re.findall(r'\b' + re.escape(alias) + r'\b', jd_lower, re.I))
        scores[kw] = count
    return scores


def _reorder_bullets_by_relevance(experience_text: str, jd_kws: set) -> str:
    """Reorder experience bullets so the most JD-relevant ones appear first.
    This is pure programmatic — no LLM needed and very effective for ATS."""
    if not experience_text:
        return experience_text

    lines = experience_text.split("\n")
    # Split into blocks (each job = consecutive lines until blank or new company)
    blocks = []
    current = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    # Within each block, keep header (company/date line) fixed, reorder bullets
    result_blocks = []
    for block in blocks:
        if len(block) <= 2:
            result_blocks.append(block)
            continue
        # First 1-2 lines = company / title header — keep fixed
        header_end = 0
        for i, line in enumerate(block):
            stripped = line.strip().lstrip("-•* ")
            if not stripped.startswith("-") and i < 2:
                header_end = i + 1
            else:
                break
        header = block[:header_end]
        bullets = block[header_end:]

        # Score each bullet by keyword overlap with JD
        scored = []
        for b in bullets:
            b_lower = b.lower()
            score = sum(1 for kw in jd_kws if kw in b_lower)
            scored.append((score, b))
        scored.sort(key=lambda x: x[0], reverse=True)
        result_blocks.append(header + [b for _, b in scored])

    return "\n\n".join("\n".join(b) for b in result_blocks)


# ─────────────────────────────────────────────────────────────────────────────
# RESUME ANALYSIS — tech-skill-only scoring + must-have detection
# ─────────────────────────────────────────────────────────────────────────────

def _extract_keyword_context(jd_text: str, keyword: str) -> str:
    """Return the most informative JD sentence containing this keyword (max 140 chars).
    Used to give HR users context on WHY a keyword matters for this specific role."""
    kw_lower = keyword.lower()
    sentences = re.split(r'[.\n;!?]', jd_text)
    best, best_len = "", 0
    for sent in sentences:
        s = sent.strip()
        if kw_lower in s.lower() and 15 < len(s) < 250:
            # Prefer sentences that are informative (longer, not just a heading)
            if len(s) > best_len:
                best, best_len = s, len(s)
    return best[:140] if best else ""


def _build_jd_cooccurrence(jd_text: str, keywords: set) -> dict:
    """Map each keyword to the set of other keywords it co-occurs with in the same JD sentence.
    Higher co-occurrence = stronger contextual relationship in THIS JD.
    Purely statistical — no domain knowledge needed."""
    cooccur: dict = {kw: set() for kw in keywords}
    jd_lower = jd_text.lower()
    for sent in re.split(r'[.\n;!?]', jd_lower):
        present = {kw for kw in keywords if kw in sent}
        for kw in present:
            cooccur[kw].update(present - {kw})
    return cooccur


def _enhance_experience_with_cooccurrence(
    experience_text: str,
    important_missing: list,
    all_jd_kws: set,
    cooccur: dict,
) -> str:
    """Inject missing JD keywords into existing experience bullets using JD co-occurrence.

    Logic: if a bullet already mentions keyword A, and A co-occurs with missing keyword B
    in the JD, then B is contextually relevant to this bullet and safe to inject.

    This is purely programmatic — no LLM, no domain knowledge.
    Works for any domain and significantly improves score by covering ALL bullet points.
    """
    if not experience_text or not important_missing:
        return experience_text

    lines = experience_text.split("\n")
    result = []
    used: set = set()
    pool = list(important_missing)

    for line in lines:
        stripped = line.strip()
        is_bullet = stripped and stripped[0] in ('-', '•', '*', '–')
        if is_bullet and len(stripped) > 20:
            line_lower = stripped.lower()
            # Keywords the bullet already mentions
            bullet_kws = {kw for kw in all_jd_kws if kw in line_lower}
            if bullet_kws:
                best_kw, best_score = None, 0
                for mkw in pool:
                    if mkw in line_lower or mkw in used:
                        continue
                    # Co-occurrence score = how many of this bullet's keywords
                    # appear in the same JD sentences as the missing keyword
                    score = len(cooccur.get(mkw, set()) & bullet_kws)
                    if score > best_score:
                        best_score, best_kw = score, mkw
                if best_kw and best_score >= 1:
                    line = line.rstrip()
                    if line.endswith('.'):
                        line = line[:-1] + f", {best_kw}."
                    else:
                        line = line + f", {best_kw}"
                    used.add(best_kw)
                    pool = [w for w in important_missing if w not in used]
        result.append(line)
    return "\n".join(result)


def _extract_required_context(jd_text: str) -> set:
    """Return all meaningful keywords that appear near required/must/essential language in JD.
    Works for any domain — no tech-specific knowledge needed."""
    required_kws: set = set()
    sentences = re.split(r'[.\n;]', jd_text)
    for sent in sentences:
        if re.search(r'\b(required|must have|must-have|essential|mandatory|minimum|critical)\b', sent, re.I):
            for w in re.findall(r'\b[A-Za-z][A-Za-z0-9+#.]{2,}\b', sent):
                nw = _normalise(w.lower())
                if nw not in _GRAMMAR_WORDS and len(nw) >= 3:
                    required_kws.add(nw)
    return required_kws


def _suggest_project_enhancements(resume_text: str, tech_missing: set) -> list:
    """For each existing resume project, suggest which missing JD keywords could be added."""
    if not tech_missing:
        return []
    suggestions = []
    proj_matches = re.findall(r'(?:Project\s*[:\-]?\s*)([^\n|]{5,60})', resume_text, re.I)
    if not proj_matches:
        return []
    groups = _group_by_domain(list(tech_missing))
    for proj_name in proj_matches[:4]:
        proj_name = proj_name.strip()
        idx = resume_text.lower().find(proj_name.lower())
        if idx == -1:
            continue
        proj_context = resume_text[max(0, idx - 30): idx + 400].lower()
        # What domains does this project already touch?
        proj_domains: set = set()
        for domain, kws in groups.items():
            if any(kw in proj_context for kw in kws):
                proj_domains.add(domain)
        # Suggest keywords from same domain or any domain if project is generic
        suggest = []
        for domain, kws in groups.items():
            if domain in proj_domains or not proj_domains:
                for kw in kws:
                    if kw not in proj_context:
                        suggest.append(kw)
        if suggest:
            suggestions.append({
                "project":      proj_name,
                "add_keywords": suggest[:4],
            })
    return suggestions[:3]


def analyze_resume(resume_text: str, jd_text: str):
    """
    Scores resume against JD using frequency-based keyword matching.
    Works for ANY domain (QA, DevOps, Finance, Sales, etc.) without code changes.
    Score = what % of the JD's meaningful keywords appear in the resume.
    MUST HAVE = keyword repeated 2+ times in JD, or in required/essential context.
    NICE TO HAVE = keyword mentioned once in JD.
    """
    try:
        resume_text = _strip_contact_pii(resume_text)
        if not jd_text or len(jd_text.strip()) < 30:
            return {"success": False, "error": "JD text is too short or empty — please add more detail to the job description"}

        jd_kws  = _extract_keywords(jd_text)
        res_kws = _extract_keywords(resume_text)

        if not jd_kws:
            return {"success": False, "error": "JD text is too short or empty — please add more detail to the job description"}

        matched = jd_kws & res_kws
        missing = jd_kws - res_kws

        # Score = keyword coverage (achievable near 100% for strong match)
        score       = round(len(matched) / len(jd_kws) * 100)
        match_level = "High" if score >= 75 else "Medium" if score >= 50 else "Low"

        # Frequency of each JD keyword — determines importance, purely statistical
        freq        = _score_keywords_by_jd_freq(jd_text, jd_kws)
        req_context = _extract_required_context(jd_text)

        # Classify missing keywords: MUST HAVE vs NICE TO HAVE
        # MUST HAVE: repeated 2+ times in JD OR in required/essential sentences
        # NICE TO HAVE: mentioned once
        must_have, nice_to_have = [], []
        for kw in missing:
            f = freq.get(kw, 0)
            if f >= 2 or kw in req_context:
                must_have.append((kw, f))
            else:
                nice_to_have.append((kw, f))

        must_have.sort(   key=lambda x: x[1], reverse=True)
        nice_to_have.sort(key=lambda x: x[1], reverse=True)

        # Strengths — matched keywords sorted by JD frequency (most valued first)
        strengths = sorted(matched, key=lambda w: freq.get(w, 0), reverse=True)[:12]

        # Build gap list with JD context snippets so HR/recruiter understands WHY each item matters
        gaps = []
        for kw, f in must_have[:8]:
            freq_note = f"(appears {f}x)" if f > 1 else ""
            ctx = _extract_keyword_context(jd_text, kw)
            label = f"[MUST HAVE] {kw} {freq_note}"
            if ctx:
                label += f" | JD says: \"{ctx}\""
            gaps.append(label)
        for kw, f in nice_to_have[:6]:
            ctx = _extract_keyword_context(jd_text, kw)
            label = f"[NICE TO HAVE] {kw}"
            if ctx:
                label += f" | JD says: \"{ctx}\""
            gaps.append(label)

        # Recommendations — actionable per-keyword guidance
        recommendations = []
        for kw, f in must_have[:6]:
            ctx = _extract_keyword_context(jd_text, kw)
            freq_note = f" (mentioned {f} times in JD)" if f > 1 else ""
            fix_parts = [f"Add '{kw}' to your Skills section if you have this experience."]
            if _is_tech_keyword(kw):
                fix_parts.append(f"Also mention '{kw}' in at least one experience bullet or project.")
            if ctx:
                fix_parts.append(f"JD context: \"{ctx[:100]}\"")
            recommendations.append({
                "section":  "Skills + Experience",
                "priority": "MUST HAVE",
                "issue":    f"'{kw}' missing{freq_note} — CV may be screened out without it",
                "fix":      "  ".join(fix_parts),
            })

        # Project suggestions — filter to tech keywords for injection relevance
        tech_missing_for_proj = {kw for kw, _ in must_have[:10] if _is_tech_keyword(kw)}
        proj_suggestions = _suggest_project_enhancements(resume_text, tech_missing_for_proj)

        # ── Semantic soft-gap detection ────────────────────────────────────────
        # For each MUST HAVE gap, check if the resume has semantically similar content.
        # Hard gap  = genuinely missing (no related content found in resume)
        # Soft gap  = keyword absent but related phrasing exists → just needs rephrasing
        must_have_kws = [kw for kw, _ in must_have]
        sem_result    = detect_soft_gaps(resume_text, must_have_kws, jd_text)
        sem_score_val = _semantic_score(resume_text, jd_text)

        # Domain detection — tells UI whether injection is supported
        domain_info = detect_jd_domain(jd_text)
        limitation_msg = None
        if not domain_info["injection_supported"]:
            limitation_msg = (
                f"Domain detected: {domain_info['domain']}. "
                f"Keyword analysis and gap detection work for all domains. "
                f"Skill injection is currently optimised for tech roles. "
                f"Contact the system administrator to request full support for {domain_info['domain']} roles."
            )

        # Role / function compatibility check
        candidate_fn = detect_function(resume_text)
        jd_fn        = detect_function(jd_text)
        compat       = check_role_compatibility(candidate_fn, jd_fn)

        # Extract job title from resume header (line after name)
        _sec = _parse_sections_simple(resume_text)
        detected_role = _sec.get("role", "")

        return {
            "success": True,
            "data": {
                "score":               score,
                "match_level":         match_level,
                "strengths":           [f"Has: {w}" for w in strengths],
                "gaps":                gaps,
                "recommendations":     recommendations,
                "must_have_missing":   [kw for kw, _ in must_have],
                "nice_to_have_missing":[kw for kw, _ in nice_to_have[:8]],
                "project_suggestions": proj_suggestions,
                "jd_tech_count":       len(jd_kws),
                "final_resume":        "",
                "detected_domain":     domain_info["domain"],
                "injection_supported": domain_info["injection_supported"],
                "domain_confidence":   domain_info["confidence"],
                "limitation_msg":      limitation_msg,
                # Semantic
                "semantic_score":      sem_score_val,
                "semantic_tier":       sem_result.get("tier", "none"),
                "soft_gaps":           sem_result.get("soft_gaps", []),
                "true_hard_gaps":      sem_result.get("true_gaps", must_have_kws),
                # Role compatibility
                "candidate_function":  candidate_fn,
                "jd_function":         jd_fn,
                "role_compatibility":  compat,
                "detected_role":       detected_role,
            },
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Section parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_sections_simple(text: str) -> dict:
    text = clean_resume_text(text)
    sec = {
        "name": "", "role": "", "email": "", "mobile": "",
        "location": "", "linkedin": "",
        "summary": "", "skills": "", "experience": "",
        "projects": "", "education": "", "certifications": "",
    }
    lines = [l.strip() for l in text.split("\n")]

    for line in lines[:20]:
        if not line:
            continue
        # Two-column PDFs put name + phone on same line — extract each before testing
        m_email  = re.search(r'[\w._%+\-]+@[\w.\-]+\.[a-z]{2,}', line, re.I)
        m_phone  = re.search(r'(\+91[\s\-]?)?[6-9]\d{9}|\+\d[\d\s\-]{8,14}', line)
        m_linked = re.search(r'linkedin\.com\S*', line, re.I)
        m_loc    = re.search(r'\b(pune|mumbai|bangalore|hyderabad|chennai|delhi|noida|gurgaon|kolkata|india)\b', line, re.I)
        if m_email  and not sec["email"]:   sec["email"]   = m_email.group()
        if m_phone  and not sec["mobile"]:  sec["mobile"]  = m_phone.group()
        if m_linked and not sec["linkedin"]:sec["linkedin"] = m_linked.group()
        if m_loc    and not sec["location"]:sec["location"] = line.strip()
        # Name = first short line that is title-case words only (strip out any inline contact info first)
        stripped = re.sub(r'[\w._%+\-]+@[\w.\-]+\.[a-z]{2,}', '', line, flags=re.I)
        stripped = re.sub(r'(\+91[\s\-]?)?[6-9]\d{9}|\+\d[\d\s\-]{8,14}', '', stripped)
        stripped = re.sub(r'linkedin\.com\S*', '', stripped, flags=re.I)
        stripped = re.sub(r'[#ï@|•·]+', '', stripped).strip()
        if not sec["name"] and re.match(r'^[A-Z][a-zA-Z .]{2,40}$', stripped.strip()):
            sec["name"] = stripped.strip()
        elif sec["name"] and not sec["role"]:
            role_cand = stripped.strip()
            if role_cand and len(role_cand) < 80 and not re.search(r'\d{5,}', role_cand):
                sec["role"] = role_cand

    kw_map = {
        # Summary / Profile
        "summary": "summary", "professional summary": "summary",
        "objective": "summary", "profile": "summary", "about me": "summary",
        "career objective": "summary", "professional profile": "summary",
        "professional overview": "summary", "executive summary": "summary",
        # Skills
        "skills": "skills", "technical skills": "skills",
        "key skills": "skills", "core competencies": "skills", "expertise": "skills",
        "technical expertise": "skills", "core skills": "skills",
        "computer skills": "skills", "it skills": "skills",
        "technologies": "skills", "tech stack": "skills",
        "skill set": "skills", "technical profic": "skills",
        "tools & technologies": "skills", "tools and technologies": "skills",
        "technology stack": "skills",
        # Experience
        "experience": "experience", "work experience": "experience",
        "professional experience": "experience", "employment": "experience",
        "work history": "experience", "career history": "experience",
        "employment history": "experience", "career summary": "experience",
        "professional background": "experience",
        # Projects
        "project": "projects", "project summary": "projects", "projects": "projects",
        "key projects": "projects", "personal projects": "projects",
        "portfolio": "projects", "notable projects": "projects",
        # Education
        "education": "education", "academic": "education", "qualification": "education",
        "educational background": "education", "academic background": "education",
        "academic profile": "education", "educational qualification": "education",
        # Certifications
        "certif": "certifications", "certification": "certifications",
        "achievement": "certifications", "award": "certifications",
        "training": "certifications", "courses": "certifications",
        "licenses": "certifications",
    }

    current = None
    buf: dict = {}
    for line in lines:
        lower = line.lower()
        matched_sec = None
        for kw, sname in kw_map.items():
            if lower.startswith(kw) and len(line) < 60:
                matched_sec = sname
                break
        if matched_sec:
            current = matched_sec
            buf.setdefault(current, [])
        elif current and line:
            buf.setdefault(current, []).append(line)

    for k, v in buf.items():
        sec[k] = "\n".join(v).strip()

    return sec


# ─────────────────────────────────────────────────────────────────────────────
# JD ALIGNMENT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _group_by_domain(keywords: list) -> dict:
    """Group TECH-ONLY keywords by domain. Drops non-tech words silently."""
    groups: dict = {
        "Cloud & DevOps":   [],
        "Backend & APIs":   [],
        "AI & ML":          [],
        "Data & Databases": [],
        "Frontend":         [],
        "CI/CD & Tools":    [],
        "Testing & QA":     [],
    }
    for kw in keywords:
        if not _is_tech_keyword(kw):
            continue  # drop non-tech words entirely
        kl = kw.lower().replace("-", "").replace("/", "").replace(" ", "")
        if kl in _CLOUD or any(s in kl for s in ['cloud','kube','docker','terra','helm','argocd','lambda','fargate','eks','ecs']):
            groups["Cloud & DevOps"].append(kw)
        elif kl in _AI or any(s in kl for s in ['llm','rag','embed','vector','prompt','openai','langchain','hugging','finetun','mlflow','sagemaker']):
            groups["AI & ML"].append(kw)
        elif kl in _BACKEND or any(s in kl for s in ['api','micro','service','spring','boot','node','grpc','graphql','rest','queue','broker']):
            groups["Backend & APIs"].append(kw)
        elif kl in _DATA or any(s in kl for s in ['sql','db','data','base','mongo','redis','spark','kafka','etl','warehouse','lake']):
            groups["Data & Databases"].append(kw)
        elif kl in _FRONTEND or any(s in kl for s in ['react','angular','vue','front','ui','css','html','webpack','next','svelte']):
            groups["Frontend"].append(kw)
        elif kl in _DEVOPS or any(s in kl for s in ['ci','cd','jenkins','sonar','jira','prometheus','grafana','elk','observ']):
            groups["CI/CD & Tools"].append(kw)
        elif kl in _TESTING or any(s in kl for s in ['test','junit','pytest','selenium','mock','cypress','playwright']):
            groups["Testing & QA"].append(kw)
        else:
            # Only add if it still passed _is_tech_keyword
            groups.setdefault("Other Tools", []).append(kw)

    return {k: v for k, v in groups.items() if v}


def _best_skill_category(kw: str, skills_obj: dict) -> str:
    """Return the most appropriate category name for injecting a keyword.
    Prefers an existing category whose name overlaps with the keyword's domain.
    Falls back to the domain label itself, or 'Cloud & Tools' if uncategorizable."""
    domain_map = _group_by_domain([kw])
    if not domain_map:
        return "Cloud & Tools"
    domain_label = next(iter(domain_map))
    if domain_label == "Other Tools":
        return "Cloud & Tools"

    # Try to match against an existing category by shared words
    domain_words = set(re.sub(r'[&/]', ' ', domain_label).lower().split())
    best_cat, best_overlap = None, 0
    for cat in skills_obj:
        cat_words = set(re.sub(r'[&/]', ' ', cat).lower().split())
        overlap = len(domain_words & cat_words)
        if overlap > best_overlap:
            best_overlap, best_cat = overlap, cat

    return best_cat if (best_cat and best_overlap >= 1) else domain_label


def _inject_skills(skills_text: str, missing_kws: list) -> str:
    """Add ONLY real tech keywords missing from resume into skills section."""
    existing_lower = skills_text.lower()

    # Filter to genuine tech keywords not already present
    to_add = [
        k for k in missing_kws
        if _is_tech_keyword(k) and k.lower() not in existing_lower
    ]
    if not to_add:
        return skills_text

    lines = [l.strip() for l in skills_text.split("\n") if l.strip()]
    groups = _group_by_domain(to_add)

    for domain, kws in groups.items():
        if domain == "Other Tools":
            continue  # skip uncategorized even if they passed the tech filter
        lines.append(f"{domain}: {', '.join(kws)}")

    return "\n".join(lines)


def _add_project_for_tech(domain: str, tech_kws: list) -> str:
    """Generate a project entry for a known tech domain. Falls back programmatically."""
    if not tech_kws:
        return ""

    tech = ", ".join(tech_kws[:4])
    prompt = (
        f"Task: Write a resume project entry. Output ONLY the entry, nothing else.\n"
        f"Format (follow exactly):\n"
        f"Project: SmartAPI Platform | Tech: {tech}\n"
        f"- Built {tech_kws[0]}-based service handling 10k daily requests\n"
        f"- Reduced latency by 30% using {tech_kws[1] if len(tech_kws) > 1 else tech_kws[0]}\n"
        f"Now write one for technologies: {tech}\n"
        f"Output:"
    )
    try:
        result = _call_llm(prompt, max_tokens=90)
    except RuntimeError:
        result = ""

    if result and len(result) > 25 and "-" in result and "Project:" in result:
        # Sanity-check: must not have hallucinated bad content
        lines = [l for l in result.split("\n") if l.strip()]
        return "\n".join(lines[:4])

    # Programmatic fallback using actual tech names
    project_name = f"{tech_kws[0].title()} {domain.split('&')[0].strip()} Project"
    lines = [f"Project: {project_name} | Tech: {tech}"]
    for kw in tech_kws[:3]:
        lines.append(f"- Implemented {kw}-based solution to deliver scalable, production-ready capabilities")
    return "\n".join(lines)


def _parse_json_response(text: str) -> dict | None:
    """Parse JSON from LLM response, handling markdown code blocks and minor noise."""
    if not text:
        return None
    t = re.sub(r'^```(?:json)?\s*\n?', '', text.strip(), flags=re.I)
    t = re.sub(r'\n?```\s*$', '', t.strip()).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r'\{[\s\S]*\}', t)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None


def _extract_resume_structured(resume_text: str) -> dict | None:
    """Use LLM to extract resume into structured JSON — handles any resume format.
    Returns None on failure. Only called when a capable provider is active."""
    resume_text = _strip_contact_pii(resume_text)
    cleaned = clean_resume_text(resume_text)
    snip = _truncate(cleaned, _provider_extract_limit())
    prompt = (
        'Extract ALL content from this resume into structured JSON. Copy every job, project, education entry exactly.\n'
        'Output ONLY valid JSON — no markdown, no explanation, no text before or after the JSON.\n'
        'IMPORTANT rules:\n'
        '- Under "experience": for each EMPLOYER company, create ONE entry.\n'
        '  - "title" = the actual job title held at that company (e.g. "QA Engineer", "Software Developer")\n'
        '  - "period" = OVERALL employment dates at that company (e.g. "Apr 2021 – Till Date")\n'
        '  - "projects" = list of ALL named projects done at that company, each with:\n'
        '      * "name"    = project name exactly as written\n'
        '      * "tech"    = comma-separated tools/technologies used in that project\n'
        '      * "period"  = project duration exactly as written (e.g. "Nov 2025 – Till Date")\n'
        '      * "bullets" = ONLY actual work achievement sentences for that project\n'
        '        · Start with a verb or describe what was done\n'
        '        · DO NOT include project name, tech list, or section headings as bullets\n'
        '  - "bullets" = any company-level bullets NOT belonging to a specific project (can be empty [])\n'
        '- Under "projects" (top-level): ONLY standalone/personal/academic projects with no employer company.\n'
        '  Leave this array empty [] if all projects belong to a company.\n'
        '- Under "skills": list only actual tool/technology names. No verbs, no phrases.\n'
        '- Under "education": degrees, diplomas, AND any professional training/courses from the Academic Profile table. No personal details (DOB, gender, address, marital status).\n'
        '- SKIP any line that is: "I am responsible for", "Description:", "Technologies & Tools:", "Roles & Responsibilities:", or a bare project heading\n'
        '- Include ALL companies from the entire resume — do not stop early.\n'
        'Schema:\n'
        '{"name":"","role":"","email":"","mobile":"","location":"","linkedin":"",'
        '"summary":"",'
        '"skills":{"Generative AI":["tool1"],"Languages":["lang1"],"Frameworks":["fw1"],"Databases":["db1"],"Cloud & Tools":["tool1"]},'
        '"experience":[{"company":"company name","title":"job title","period":"company dates","bullets":[],'
        '"projects":[{"name":"project name","tech":"tools","period":"project dates","bullets":["achievement"]}]}],'
        '"projects":[],'
        '"education":["Degree | Institution | Year"],'
        '"certifications":["cert name"]}\n'
        'SKILLS NOTE: If the resume already has labeled skill categories, preserve them exactly. '
        'If not, group skills into logical categories. Skills values must be tool/tech names only.\n'
        f'Resume:\n{snip}\n'
        'JSON:'
    )
    result = ""
    try:
        result = _call_llm(prompt, max_tokens=4500)
    except RuntimeError:
        return None
    data = _parse_json_response(result)
    if not isinstance(data, dict):
        return None
    if not data.get("name") and not data.get("experience"):
        return None
    return data


def _rewrite_structured_with_jd(structured: dict, jd_text: str, jd_kws: set, freq_scores: dict,
                                 resume_text: str = "") -> dict:
    """Rewrite every section of the structured resume with JD context.
    Each section gets its own focused LLM call — no section is left unoptimised.
    resume_text is used for resume-verified skill injection: any JD keyword that
    actually appears in the original resume is injected regardless of taxonomy."""
    import copy
    result = copy.deepcopy(structured)
    all_jd_freq = {kw: freq_scores.get(kw, 0) for kw in jd_kws}
    resume_lower = resume_text.lower() if resume_text else ""

    # Summary — rewrite with full JD context, bridging any skill gaps
    if result.get("summary"):
        jd_snip = _truncate(jd_text, 350)
        prompt = (
            f"Rewrite this professional summary for {result.get('name','the candidate')} "
            f"targeting this job:\n{jd_snip}\n\n"
            f"Original summary: {_truncate(result['summary'], 300)}\n\n"
            f"Rules:\n"
            f"- 3 sentences, 55-75 words total.\n"
            f"- Sentence 1: years of experience + core expertise that directly matches JD.\n"
            f"- Sentence 2: strongest relevant project/achievement from background.\n"
            f"- Sentence 3: bridge any gap — if JD requires a tool the candidate lacks, "
            f"mention the CLOSEST related tool they DO have (e.g. ServiceNow ≈ Salesforce, "
            f"RAG chatbot ≈ Amazon Connect knowledge base). Frame as transferable.\n"
            f"- Match JD keywords naturally. No generic filler phrases.\n"
            f"Output ONLY the summary text, no labels."
        )
        try:
            r = _call_llm(prompt, max_tokens=100)
            if r and len(r) > 20:
                result["summary"] = re.sub(r'^(Summary|Output)[:\s]*', '', r, flags=re.I).strip()
        except RuntimeError:
            pass

    # Experience — rewrite bullets for each job
    for job in result.get("experience", []):
        bullets = job.get("bullets", [])
        if not bullets:
            continue
        # Keep only the most JD-relevant bullets — scores each bullet by keyword overlap,
        # caps at _MAX_BULLETS_PER_COMPANY (best practice) so no further truncation needed.
        if len(bullets) > _MAX_BULLETS_PER_COMPANY:
            scored = sorted(
                bullets,
                key=lambda b: sum(1 for kw in jd_kws if kw in b.lower()),
                reverse=True,
            )
            bullets = scored[:_MAX_BULLETS_PER_COMPANY]
            job["bullets"] = bullets
        relevant = _find_relevant_jd_kws(" ".join(bullets), jd_kws, all_jd_freq, top_n=8)
        kw_str = ", ".join(k for k in relevant if _is_tech_keyword(k)) or ", ".join(relevant[:5])
        bullets_str = "\n".join(f"- {b}" for b in bullets)
        prompt = (
            f"Rewrite these resume bullets for {job.get('title','')} at {job.get('company','')}.\n"
            f"JD keywords to weave in where they naturally fit: {kw_str}\n"
            f"Original bullets (keep ALL of them, preserve metrics and project names):\n{bullets_str}\n"
            f"Rules:\n"
            f"- Keep every bullet. Do NOT drop or merge bullets.\n"
            f"- Preserve all numbers, percentages, project names, and company names exactly.\n"
            f"- Only add JD keywords where they fit naturally — do not force them.\n"
            f"- Start every bullet with a strong action verb: Led, Built, Automated, Validated, Reduced, Designed, Implemented, Executed, Delivered, Optimised.\n"
            f"- NEVER add numbers, percentages, or metrics that are not explicitly stated in the original bullet.\n"
            f"- If the original has a number (e.g. '500+ test cases'), keep it exactly. If not, do not invent one.\n"
            f"- Under 25 words each.\n"
            f"Output ONLY the rewritten bullets, one per line starting with '- '."
        )
        try:
            r = _call_llm(prompt, max_tokens=600)
            if r and "-" in r and len(r) > 20:
                nb = [l.strip().lstrip("-•* ") for l in r.split("\n") if l.strip() and l.strip()[0] in "-•*"]
                # Only accept rewrite if it has enough bullets AND avg length is substantial
                avg_len = sum(len(b) for b in nb) / max(len(nb), 1)
                if len(nb) >= max(1, len(bullets) // 2) and avg_len > 25:
                    # Deduplicate near-identical bullets (>70% word overlap)
                    deduped = []
                    for b in nb:
                        bwords = set(b.lower().split())
                        if not any(
                            len(bwords & set(existing.lower().split())) / max(len(bwords | set(existing.lower().split())), 1) > 0.7
                            for existing in deduped
                        ):
                            deduped.append(b)
                    job["bullets"] = deduped
                # else: keep original bullets (LLM produced thin/generic output)
        except RuntimeError:
            pass

    # Projects — priority-driven rewrite applied to ALL projects:
    #   - Nested projects inside each experience entry (company-linked, with period)
    #   - Standalone top-level projects (personal/academic, no employer)
    #
    # Primary  = JD keywords with freq >= 3 (core requirement — must appear)
    # Secondary = JD keywords with freq 1-2 (mentioned — use if they fit)
    # Only use real tech keywords in bullet/title prompts — plain English words like
    # "semantic", "given", "health", "domain", "extract" must never be force-injected.
    jd_primary   = sorted([kw for kw in jd_kws
                            if all_jd_freq.get(kw, 0) >= 3 and _is_tech_keyword(kw)
                            and kw.lower() not in _COMMON_WORDS],
                           key=lambda w: all_jd_freq.get(w, 0), reverse=True)[:6]
    jd_secondary = sorted([kw for kw in jd_kws
                            if 1 <= all_jd_freq.get(kw, 0) < 3 and _is_tech_keyword(kw)
                            and kw.lower() not in _COMMON_WORDS],
                           key=lambda w: all_jd_freq.get(w, 0), reverse=True)[:4]

    import datetime as _dt
    _CURRENT_YEAR = _dt.datetime.now().year

    def _parse_proj_year(period: str) -> int:
        """Extract the end year from a project period string.
        Returns _CURRENT_YEAR for 'till date'/'present', 0 if unparseable."""
        if not period:
            return _CURRENT_YEAR
        p = period.lower()
        if any(x in p for x in ("till date", "present", "current", "ongoing", "now")):
            return _CURRENT_YEAR
        years = re.findall(r'\b(19[89]\d|20[0-3]\d)\b', period)
        return int(years[-1]) if years else _CURRENT_YEAR

    def _rewrite_one_project(proj: dict) -> None:
        """In-place rewrite of a single project dict.
        Legacy projects (>5 years old) keep their original language — we never
        inject modern tools or force JD keywords into past work."""
        bullets      = proj.get("bullets", [])
        orig_name    = proj.get("name", "")
        orig_tech    = proj.get("tech", "")
        orig_period  = proj.get("period", "")   # always preserved
        proj_context = f"{orig_name} {orig_tech} " + " ".join(bullets)
        proj_lower   = proj_context.lower()

        # Temporal guard: if project ended more than 5 years ago, keep as-is.
        proj_year = _parse_proj_year(orig_period)
        if proj_year < _CURRENT_YEAR - 5:
            return  # legacy project — preserve original content entirely

        primary_hits  = sum(1 for kw in jd_primary if kw in proj_lower)
        primary_str   = ", ".join(jd_primary)   if jd_primary   else ""
        secondary_str = ", ".join(jd_secondary) if jd_secondary else ""

        # ── 1. Context-aware title rewrite ───────────────────────────────────
        # Keep original name when it already signals the JD domain.
        # Rewrite only when the name is a generic/internal label.
        name_has_primary = any(kw in orig_name.lower() for kw in jd_primary)
        if not name_has_primary and primary_hits < 2 and orig_name:
            title_prompt = (
                f"You are rewriting a resume project title to be context-oriented for a recruiter.\n"
                f"Original title: \"{orig_name}\"\n"
                f"What the project involved (bullets): {_truncate(' '.join(bullets), 200)}\n"
                f"JD primary skills: {primary_str}\n"
                f"Rules:\n"
                f"- Output ONLY the new title, nothing else — no explanation, no labels.\n"
                f"- Keep it short: 4-8 words.\n"
                f"- Make it descriptive of what was actually done (not a generic label).\n"
                f"- Naturally include 1-2 primary JD skills if they genuinely fit the work.\n"
                f"- If the original title is already descriptive and relevant, output it unchanged.\n"
                f"- NEVER invent work that is not in the bullets."
            )
            try:
                new_name = _call_llm(title_prompt, max_tokens=20)
                if new_name:
                    new_name = re.sub(r'^(Title|Output|Project)[:\s]*', '', new_name, flags=re.I).strip().strip('"\'')
                    if 3 <= len(new_name.split()) <= 12 and len(new_name) > 4:
                        proj["name"] = new_name
            except RuntimeError:
                pass

        # ── 2. Priority-driven bullet rewrite ────────────────────────────────
        bullets_str = "\n".join(f"- {b}" for b in bullets) if bullets else "(no bullets)"
        bullet_prompt = (
            f"Rewrite these project bullets for a resume targeting this job.\n"
            f"Project: {proj.get('name', orig_name)}\n"
            f"PRIMARY JD skills (must appear in rewrite where they genuinely fit): {primary_str}\n"
            f"SECONDARY JD skills (use if they fit naturally): {secondary_str}\n"
            f"Original bullets:\n{_truncate(bullets_str, 400)}\n"
            f"Rules:\n"
            f"- Lead the most important bullets with PRIMARY skills — frame the work around them.\n"
            f"- If the candidate used a similar tool (e.g. Selenium when JD wants Playwright), "
            f"bridge it: mention both or describe the transferable skill.\n"
            f"- Keep all original metrics, numbers, and project names exactly.\n"
            f"- NEVER add numbers or metrics not in the original bullets.\n"
            f"- 3-5 bullets. Each starts with a strong action verb.\n"
            f"- Under 25 words each.\n"
            f"Output ONLY the bullets, one per line starting with '- '."
        )
        try:
            r = _call_llm(bullet_prompt, max_tokens=300)
            if r and "-" in r and len(r) > 20:
                nb = [l.strip().lstrip("-•* ") for l in r.split("\n") if l.strip() and l.strip()[0] in "-•*"]
                avg_len = sum(len(b) for b in nb) / max(len(nb), 1)
                # Accept rewrite if LLM returned ≥3 bullets and they're substantial.
                # For large bullet lists (>8), LLM is asked for 3-5 — don't require half.
                min_needed = 3 if len(bullets) > 8 else max(1, len(bullets) // 2)
                if len(nb) >= min_needed and avg_len > 20:
                    proj["bullets"] = nb
        except RuntimeError:
            pass

        # ── 3. Tech stack: primary JD tools first, then existing, then secondary ──
        # Only NAMED TOOLS go into the Tech: line — activity/methodology words
        # (testing, automation, release, regression, deployment…) are excluded even
        # if they pass _is_tech_keyword, because they describe what was done, not
        # which platform was used.
        existing_tech_lower = orig_tech.lower()
        new_primary_tech = [
            kw.title() for kw in jd_primary
            if _is_tech_keyword(kw) and kw not in existing_tech_lower
            and _is_named_tool_kw(kw)
        ]
        new_secondary_tech = [
            kw.title() for kw in jd_secondary
            if _is_tech_keyword(kw) and kw not in existing_tech_lower
            and kw not in [t.lower() for t in new_primary_tech]
            and _is_named_tool_kw(kw)
        ]
        tech_parts = list(new_primary_tech)
        if orig_tech.strip():
            tech_parts.append(orig_tech.strip())
        tech_parts.extend(new_secondary_tech[:2])
        if tech_parts:
            proj["tech"] = ", ".join(tech_parts)
        # Always restore period — never let rewrite overwrite dates
        if orig_period:
            proj["period"] = orig_period

    def _jd_score(proj: dict) -> int:
        """Count how many JD primary keywords appear in project name + tech + bullets."""
        ctx = (proj.get("name", "") + " " + proj.get("tech", "") + " "
               + " ".join(proj.get("bullets", []))).lower()
        return sum(1 for kw in jd_primary if kw in ctx)

    # Rewrite nested projects (company-linked, main path)
    # Sort projects within each company by JD relevance BEFORE rewriting so the
    # most impactful project appears first in the output.
    for job in result.get("experience", []):
        job["projects"] = sorted(job.get("projects", []), key=_jd_score, reverse=True)
        for proj in job["projects"]:
            _rewrite_one_project(proj)

    # Rewrite standalone top-level projects (personal/academic, fallback path)
    result["projects"] = sorted(result.get("projects", []), key=_jd_score, reverse=True)
    for proj in result.get("projects", []):
        _rewrite_one_project(proj)

    # Skills — inject missing JD keywords into the skills section.
    # Two tiers:
    #   1. Resume-verified: keyword appears in the JD AND in the original resume text
    #      AND is a genuine tech term (not a common English word) — domain-agnostic
    #   2. Taxonomy-known: keyword is in the JD AND recognised by _is_tech_keyword
    # Common English words are always excluded regardless of tier.
    def _should_inject(kw: str, existing: set) -> bool:
        if kw in existing or all_jd_freq.get(kw, 0) < 1 or len(kw) < 3:
            return False
        if kw.lower() in _COMMON_WORDS:
            return False
        in_resume = bool(resume_lower) and kw in resume_lower
        # Tier 1: resume-verified — must also be a real tech term (not plain English)
        if in_resume and _is_tech_keyword(kw):
            return True
        # Tier 2: taxonomy-known gap-fill
        return _is_tech_keyword(kw)

    skills_obj = result.get("skills", [])
    if isinstance(skills_obj, dict):
        existing_lower = set()
        for vals in skills_obj.values():
            items = vals if isinstance(vals, list) else ([vals] if isinstance(vals, str) else [])
            existing_lower.update(s.lower() for s in items)
        for kw in sorted(jd_kws, key=lambda w: all_jd_freq.get(w, 0), reverse=True):
            if _should_inject(kw, existing_lower):
                display = kw.upper() if len(kw) <= 4 else kw.title()
                target_cat = _best_skill_category(kw, skills_obj)
                skills_obj.setdefault(target_cat, []).append(display)
                existing_lower.add(kw)
    else:
        existing_lower = {s.lower() for s in (skills_obj if isinstance(skills_obj, list) else [])}
        for kw in sorted(jd_kws, key=lambda w: all_jd_freq.get(w, 0), reverse=True):
            if _should_inject(kw, existing_lower):
                display = kw.upper() if len(kw) <= 4 else kw.title()
                result.setdefault("skills", []).append(display)
                existing_lower.add(kw)

    # ── Certifications — suggest Udemy courses if no relevant cert exists ────────
    # If 0 existing certifications match any jd_primary keyword, ask the LLM to
    # suggest 2 Udemy courses that close the gap. Suggestions are clearly tagged
    # so the recruiter/candidate can confirm before submission.
    existing_certs = result.get("certifications", [])
    if isinstance(existing_certs, list) and jd_primary:
        certs_text = " ".join(str(c) for c in existing_certs).lower()
        relevant_cert_count = sum(1 for kw in jd_primary if kw in certs_text)
        if relevant_cert_count == 0:
            primary_str = ", ".join(jd_primary[:5])
            cert_prompt = (
                f"Suggest exactly 2 Udemy online course certifications that would strengthen a resume "
                f"for a role requiring: {primary_str}.\n"
                f"Format each line as: Course Title - Udemy [SUGGESTED - Verify with candidate]\n"
                f"Return only 2 lines. No numbering, no explanation, no extra text."
            )
            try:
                cert_resp = _call_llm(cert_prompt, max_tokens=80).strip()
                if cert_resp:
                    suggested = [
                        line.strip() for line in cert_resp.split("\n")
                        if line.strip() and len(line.strip()) > 10
                    ][:2]
                    if suggested:
                        result.setdefault("certifications", []).extend(suggested)
            except RuntimeError:
                pass

    # ── LLM final audit pass ──────────────────────────────────────────────────
    # One targeted call: if primary JD keywords are still absent from the summary,
    # ask the LLM to weave them in with minimal other changes.
    # Guard: reject the patch if the LLM changed > 30% of the original length.
    summary_now = result.get("summary", "").strip()
    if jd_primary and len(summary_now) >= 20:
        summary_lower_now = summary_now.lower()
        still_missing = [kw for kw in jd_primary if kw.lower() not in summary_lower_now]
        if len(still_missing) >= 2:
            # Build a brief skills snippet so the LLM can reference existing expertise
            sk_ctx = ""
            sk_obj = result.get("skills", {})
            if isinstance(sk_obj, dict):
                sk_ctx = "; ".join(
                    f"{cat}: {', '.join(str(s) for s in vals[:4])}"
                    for cat, vals in sk_obj.items() if vals
                )[:250]
            elif isinstance(sk_obj, list):
                sk_ctx = ", ".join(str(s) for s in sk_obj[:12])

            missing_str = ", ".join(still_missing[:4])
            audit_prompt = (
                f"You are a resume QA editor. Revise ONLY the summary below to naturally include "
                f"the missing JD keywords. Keep all existing facts and phrasing as much as possible.\n\n"
                f"CURRENT SUMMARY:\n{summary_now}\n\n"
                f"CANDIDATE SKILLS (context only, do not copy wholesale):\n{sk_ctx}\n\n"
                f"MISSING JD KEYWORDS to weave in: {missing_str}\n\n"
                f"Rules:\n"
                f"- Return ONLY the revised summary text. No labels, no preamble.\n"
                f"- Add missing keywords naturally — 1-2 words per keyword, not a dump.\n"
                f"- Do NOT add metrics, numbers, or tools that are not already in the summary.\n"
                f"- Keep the summary under 80 words."
            )
            try:
                patched = _call_llm(audit_prompt, max_tokens=120).strip()
                patched = re.sub(r'^(Revised?|Summary|Output)[:\s]*', '', patched, flags=re.I).strip()
                if patched and len(patched) >= 20:
                    change_ratio = abs(len(patched) - len(summary_now)) / max(len(summary_now), 1)
                    if change_ratio <= 0.30:
                        result["summary"] = patched
            except RuntimeError:
                pass

    return result


def _exp_to_text(experience: list) -> str:
    lines = []
    for job in experience:
        h = " | ".join(filter(None, [job.get("company"), job.get("title"), job.get("period")]))
        if h:
            lines.append(h)
        for b in job.get("bullets", []):
            if b.strip():
                lines.append(f"- {b.strip()}")
        lines.append("")
    return "\n".join(lines).strip()


def _proj_to_text(projects: list) -> str:
    lines = []
    for proj in projects:
        tech = f" | Tech: {proj['tech']}" if proj.get("tech") else ""
        lines.append(f"Project: {proj.get('name','')}{tech}")
        for b in proj.get("bullets", []):
            if b.strip():
                lines.append(f"- {b.strip()}")
        lines.append("")
    return "\n".join(lines).strip()


def _edu_to_text(education: list) -> str:
    lines = []
    for edu in education:
        if isinstance(edu, dict):
            lines.append(" | ".join(filter(None, [edu.get("degree"), edu.get("institution"), edu.get("year")])))
        else:
            lines.append(str(edu))
    return "\n".join(lines)


_MAX_BULLETS_PER_COMPANY = 12  # ATS best practice: 5-12 bullets per role

# Common English words that appear in any resume/JD text but are NOT tech skill names.
# Excludes them from skills injection and from bullet rewrite keyword prompts.
_COMMON_WORDS: set = {
    "extract", "tech", "next", "sure", "point", "get", "set", "data", "server",
    "given", "health", "domain", "layer", "check", "run", "build", "make", "use",
    "add", "new", "old", "test", "work", "team", "good", "best", "key", "high",
    "fast", "well", "lead", "help", "need", "want", "show", "keep", "take", "put",
    "find", "look", "move", "turn", "base", "open", "free", "real", "full", "list",
    "item", "type", "rate", "date", "time", "case", "core", "part", "main", "line",
    "step", "node", "tool", "link", "view", "role", "user", "code", "call", "plan",
    "flow", "load", "file", "form", "rule", "note", "read", "send", "push", "pull",
    "map", "end", "tag", "log", "app", "web", "net", "let", "any", "all",
    "done", "info", "via", "way", "etc", "semantic", "approach", "process",
}

# Words that pass _is_tech_keyword (valid QA/DevOps activities for scoring + bullets)
# but must NEVER appear as items inside a project "Tech: X, Y" line.
# They describe what was done, not which named tool/platform was used.
_TECH_ACTIVITY_WORDS: set = {
    # QA activities
    "testing", "automation", "manual", "regression", "functional", "integration",
    "performance", "load", "stress", "sanity", "smoke", "exploratory",
    "qualityassurance", "bugtracking", "defect", "testcase", "testplan",
    "testscript", "testexecution", "testmanagement", "uiautomation", "testautomation",
    "api", "sanity", "uat",
    # DevOps activities (concepts, not named tools)
    "release", "deployment", "monitoring", "logging", "alerting", "observability",
    "security", "devops", "sre", "oncall", "incident", "pipeline", "infrastructure",
    "containerization", "automation",
}

# ── SBERT-based "named tool vs activity" classifier ───────────────────────────
# Anchors: representative examples of named tools and activity/methodology words.
# At runtime, any keyword's embedding is compared to both sets.
# If max similarity to tool anchors > max similarity to activity anchors → named tool.
# Falls back to _TECH_ACTIVITY_WORDS when SBERT (sentence-transformers) is not loaded.
_TOOL_ANCHORS = [
    "Selenium WebDriver", "Playwright", "Docker", "Jenkins", "PostgreSQL",
    "Postman", "JMeter", "Kubernetes", "React", "Python",
    "AWS", "Azure", "MongoDB", "Jira", "Snowflake", "Tableau",
]
_ACTIVITY_ANCHORS = [
    "software testing", "release management", "test automation", "deployment process",
    "monitoring systems", "regression testing", "functional testing",
    "integration testing", "performance testing", "manual testing",
    "quality assurance", "defect tracking", "pipeline management",
]
_tool_classification_cache: dict = {}


def _is_named_tool_kw(kw: str) -> bool:
    """True if kw is a named tool/platform; False if it's a generic activity/concept.
    Uses SBERT cosine similarity when available; static _TECH_ACTIVITY_WORDS as fallback."""
    kl = kw.lower().strip()
    if kl in _tool_classification_cache:
        return _tool_classification_cache[kl]

    # Static fallback — always correct for the words explicitly listed
    if kl in _TECH_ACTIVITY_WORDS:
        _tool_classification_cache[kl] = False
        return False

    # SBERT path — compare keyword embedding against tool vs activity anchors
    if _SEMANTIC_AVAILABLE and _sem_tier() == "st":
        try:
            from services.semantic_service import _st_similarities, _ensure_loaded
            _ensure_loaded()
            tool_sims = _st_similarities(kw, _TOOL_ANCHORS)
            act_sims  = _st_similarities(kw, _ACTIVITY_ANCHORS)
            result = max(tool_sims) >= max(act_sims)
            _tool_classification_cache[kl] = result
            return result
        except Exception:
            pass

    # Fallback: not in static block-list → assume it's a named tool
    _tool_classification_cache[kl] = True
    return True


def _is_capable_provider() -> bool:
    """True when the active LLM can handle complex rewrite tasks.
    Groq/NVIDIA NIM run 70B models free — they qualify alongside Gemini and Azure."""
    cfg = ai_config.load()
    return cfg.get("provider", "ollama") in ("gemini", "azure", "groq", "nvidia")


def _provider_extract_limit() -> int:
    """Safe character limit for resume extraction, scaled to the provider's context window.
    Capable providers (Groq/Gemini/Azure/NVIDIA) have 128K+ token contexts — pass the full
    resume (up to 50K chars covers even 15-page resumes).
    Small local Ollama models need aggressive truncation."""
    cfg = ai_config.load()
    if cfg.get("provider", "ollama") in ("groq", "gemini", "azure", "nvidia"):
        return 50000
    return 5000


def _split_into_blocks(text: str) -> list:
    """Split a section (experience / projects) into per-job or per-project blocks
    separated by blank lines."""
    if not text:
        return []
    blocks, current = [], []
    for line in text.split("\n"):
        if not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
            current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if len(b.strip()) > 10]


def _find_relevant_jd_kws(block_text: str, all_jd_kws: set, freq_scores: dict, top_n: int = 8) -> list:
    """Score JD keywords by relevance to a specific block.
    Already-present keywords listed first (context anchor), then highest-frequency missing ones."""
    block_lower = block_text.lower()
    present = [kw for kw in all_jd_kws if kw in block_lower]
    missing_ranked = sorted(
        (kw for kw in all_jd_kws if kw not in block_lower),
        key=lambda w: freq_scores.get(w, 0), reverse=True
    )[:max(1, top_n - len(present))]
    return present + list(missing_ranked)


def _rewrite_project_with_jd(project_text: str, jd_kws: list, jd_snippet: str) -> str:
    """Rewrite a single project block incorporating relevant JD technologies naturally.
    Only called when a capable provider (Gemini / Azure) is active."""
    kw_str = ", ".join(k for k in jd_kws if _is_tech_keyword(k)) or ", ".join(jd_kws[:6])
    proj_snip = _truncate(project_text, 400)
    prompt = (
        f"Rewrite this resume project to incorporate relevant JD technologies naturally.\n"
        f"JD technologies to consider: {kw_str}\n"
        f"Original project:\n{proj_snip}\n"
        f"Rules:\n"
        f"- Keep all facts (project name, metrics, dates) EXACTLY as given\n"
        f"- Only add JD tools that genuinely fit this project's domain — do NOT add unrelated tools\n"
        f"- 3-5 strong action-verb bullets\n"
        f"- Output ONLY the rewritten project text, nothing else\n"
        f"Rewritten:"
    )
    result = ""
    try:
        result = _call_llm(prompt, max_tokens=250)
    except RuntimeError:
        return project_text
    if result and len(result) > 30 and "-" in result:
        result = re.sub(r'^(Rewritten|Output|Project)[:\s]*', '', result, flags=re.I).strip()
        return "\n".join(l for l in result.split("\n") if l.strip())
    return project_text


def _rewrite_job_block_with_jd(job_text: str, jd_kws: list) -> str:
    """Rewrite a single job block's bullets to emphasize JD-relevant technologies.
    Preserves the job header (company / title / dates) and only rewrites bullet lines.
    Only called when a capable provider (Gemini / Azure) is active."""
    kw_str = ", ".join(k for k in jd_kws if _is_tech_keyword(k)) or ", ".join(jd_kws[:6])
    lines = job_text.split("\n")
    header_lines, bullet_lines = [], []
    for line in lines:
        if line.strip().startswith(("-", "•", "*", "–")):
            bullet_lines.append(line)
        elif not bullet_lines:
            header_lines.append(line)
        else:
            bullet_lines.append(line)
    if not bullet_lines:
        return job_text
    header = "\n".join(header_lines).strip()
    bullets_snip = _truncate("\n".join(bullet_lines), 350)
    prompt = (
        f"Rewrite these work experience bullets to highlight JD-relevant technologies.\n"
        f"JD requires: {kw_str}\n"
        f"Original bullets:\n{bullets_snip}\n"
        f"Rules:\n"
        f"- Do NOT invent new responsibilities or change scope of work\n"
        f"- Keep all metrics, numbers, and impact statements exactly\n"
        f"- Add JD tools where they naturally fit the existing work described\n"
        f"- Strong action verbs, under 20 words per bullet\n"
        f"- Output ONLY the rewritten bullets, nothing else\n"
        f"Rewritten bullets:"
    )
    result = ""
    try:
        result = _call_llm(prompt, max_tokens=220)
    except RuntimeError:
        return job_text
    if result and len(result) > 20 and "-" in result:
        result = re.sub(r'^(Rewritten|Output)[:\s]*', '', result, flags=re.I).strip()
        rewritten = "\n".join(l for l in result.split("\n") if l.strip())
        return (header + "\n" + rewritten) if header else rewritten
    return job_text


def _rephrase_soft_gaps(text: str, soft_gaps: list) -> tuple:
    """Find the best-matching line for each soft gap and inject the JD keyword term.
    Returns (modified_text, count_rephrased).
    Unlike blind injection, this targets the EXACT sentence that already covers the skill."""
    if not soft_gaps or not text:
        return text, 0
    lines = text.split("\n")
    rephrased = 0
    for sg in soft_gaps:
        keyword      = sg.get("keyword", "").strip()
        resume_match = sg.get("resume_match", "").strip()
        if not keyword or not resume_match:
            continue
        anchor = resume_match[:40].lower()
        for i, line in enumerate(lines):
            if anchor in line.lower() and keyword.lower() not in line.lower():
                line = line.rstrip()
                if line.endswith('.'):
                    lines[i] = line[:-1] + f", {keyword}."
                else:
                    lines[i] = line + f", {keyword}"
                rephrased += 1
                break
    return "\n".join(lines), rephrased


def _augment_experience_bullets(tech_kws: list) -> str:
    """Add 2 experience bullets for given tech keywords."""
    if not tech_kws:
        return ""
    tech = ", ".join(tech_kws[:3])
    prompt = (
        f"Write 2 work experience bullet points for a resume using: {tech}\n"
        f"Start each with '- ' and a strong action verb.\n"
        f"Under 25 words each. Return only the 2 bullets."
    )
    try:
        result = _call_llm(prompt, max_tokens=70)
    except RuntimeError:
        result = ""
    if result and len(result) > 15 and result.count("-") >= 2:
        bullets = [l for l in result.split("\n") if l.strip().startswith("-")][:2]
        if bullets:
            return "\n".join(bullets)

    # Fallback
    lines = []
    for kw in tech_kws[:2]:
        lines.append(f"- Implemented {kw} solutions to enhance system scalability and operational reliability")
    return "\n".join(lines)


def _build_structured_draft(s: dict, new_summary: str) -> str:
    D = DIVIDER
    out = []

    out += [D, s.get("name", ""), s.get("role", ""), D]

    contact = []
    if s.get("email"):    contact.append(f"📧 {s['email']}")
    if s.get("mobile"):   contact.append(f"📱 {s['mobile']}")
    if s.get("location"): contact.append(f"📍 {s['location']}")
    out.append("  |  ".join(contact) if contact else "")
    if s.get("linkedin"):
        out.append(f"LinkedIn: {s['linkedin']}")
    out.append("")

    if new_summary or s.get("summary"):
        out += [D, "PROFESSIONAL SUMMARY", D, new_summary or s["summary"], ""]

    if s.get("skills"):
        out += [D, "TECHNICAL SKILLS", D]
        for line in s["skills"].split("\n"):
            line = line.strip().lstrip("-•* ")
            if line:
                out.append(f"- {line}")
        out.append("")

    if s.get("experience"):
        out += [D, "WORK EXPERIENCE", D, s["experience"], ""]

    if s.get("projects"):
        out += [D, "PROJECT SUMMARY", D, s["projects"], ""]

    if s.get("education"):
        out += [D, "EDUCATION", D, s["education"], ""]

    if s.get("certifications"):
        out += [D, "CERTIFICATIONS", D]
        for line in s["certifications"].split("\n"):
            line = line.strip().lstrip("-•* ")
            if line:
                out.append(f"- {line}")

    return "\n".join(out).strip()


# ─────────────────────────────────────────────────────────────────────────────
# JD-ALIGNED RESUME GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_aligned_resume(resume_text: str, jd_text: str):
    try:
        resume_text = _strip_contact_pii(resume_text)
        resume_text = clean_resume_text(resume_text)
        sections = _parse_sections_simple(resume_text)

        jd_kws  = _extract_keywords(jd_text)
        res_kws = _extract_keywords(resume_text)
        missing = jd_kws - res_kws

        # ── Score missing keywords by frequency in JD (most-repeated = most important) ──
        # Purely statistical — works for any domain without hardcoded lists.
        freq_scores = _score_keywords_by_jd_freq(jd_text, missing)

        # Rank ALL missing keywords by JD frequency (higher = more important to role)
        # No domain filtering at this stage — frequency is the only signal needed.
        important_missing = sorted(
            missing,
            key=lambda w: (freq_scores.get(w, 0) * 10) + len(w) + (3 if re.search(r'\d', w) else 0),
            reverse=True,
        )[:20]  # top 20 by JD importance

        # ── Semantic: baseline score + classify soft vs hard gaps ────────────────
        # Soft gap  = resume already has semantically related content → rephrase it
        # Hard gap  = skill genuinely absent → inject new content
        sem_score_before = _semantic_score(resume_text, jd_text)
        sem_result   = detect_soft_gaps(resume_text, important_missing, jd_text)
        soft_gap_kws = {sg["keyword"] for sg in sem_result.get("soft_gaps", [])}
        hard_missing = [w for w in important_missing if w not in soft_gap_kws]
        tech_missing = [w for w in hard_missing if _is_tech_keyword(w)]

        # ── 1. Constrained summary rewrite for small models ───────────────────
        name     = sections.get("name", "the candidate")
        role     = sections.get("role", "")
        # If no role detected in resume, infer from JD title (first line / up to 60 chars)
        if not role:
            jd_first = jd_text.strip().split("\n")[0][:80].strip()
            role = jd_first if len(jd_first) > 3 else "Software Engineer"
        orig_sum = sections.get("summary", "")
        # Use up to 600 chars of the original summary/profile so LLM has enough facts to avoid hallucination
        res_snip = _truncate(orig_sum or resume_text, 600)

        if _is_capable_provider():
            jd_ctx = _truncate(jd_text, 300)
            summary_prompt = (
                f"Write a professional summary for {name} targeting this role.\n"
                f"JD context: {jd_ctx}\n"
                f"Candidate background (use ONLY these facts — do NOT invent stats, tools, or achievements not listed here):\n{res_snip}\n"
                f"Rules:\n"
                f"- 2-3 sentences, 50-70 words.\n"
                f"- Sentence 1: total years of experience + primary expertise from background.\n"
                f"- Sentence 2: strongest matching skill or project from background.\n"
                f"- ONLY use tools, numbers, and claims explicitly stated in the candidate background above.\n"
                f"- Do NOT mention tools or achievements from the JD unless they appear in the background.\n"
                f"Output ONLY the summary text, no labels, no explanation."
            )
        else:
            # Few-shot constrained prompt — works much better with qwen 0.5b
            summary_prompt = (
                f"Task: Rewrite a resume summary. Output ONLY the rewritten summary. No labels, no notes.\n"
                f"Example input: Java developer with 3 years in banking.\n"
                f"Example output: Results-driven Java developer with 3 years building scalable banking solutions, "
                f"specializing in microservices and high-availability systems.\n"
                f"Now rewrite for {name}, targeting: {role}\n"
                f"Original: {res_snip}\n"
                f"Output (2 sentences, under 50 words):"
            )
        try:
            new_summary = _call_llm(summary_prompt, max_tokens=80)
        except RuntimeError as e:
            return {"success": False, "error": str(e)}

        # Clean up common small-model artifacts
        if new_summary:
            new_summary = re.split(r'\s*Looking for roles?\b', new_summary, flags=re.I)[0].strip()
            new_summary = re.split(r'\s*Seeking (a|an|roles?)\b', new_summary, flags=re.I)[0].strip()
            new_summary = re.sub(r'^(Output|Rewritten summary|Summary)[:\s]*', '', new_summary, flags=re.I).strip()
            # Remove if it just repeated the example
            if "banking" in new_summary.lower() and "banking" not in resume_text.lower():
                new_summary = ""
            # Hallucination guard: if summary claims specific numbers/years not in resume, fall back
            numbers_in_summary = re.findall(r'\b\d+\b', new_summary)
            res_lower = resume_text.lower()
            for num in numbers_in_summary:
                if num not in res_lower and int(num) > 1:
                    # LLM invented a number not in the resume → discard summary
                    new_summary = ""
                    break

        if len(new_summary) < 25:
            # Fall back through the chain: original summary text → first 400 chars of resume
            new_summary = orig_sum or _truncate(resume_text, 400)

        structured_json_out = None
        soft_gaps_list = sem_result.get("soft_gaps", [])

        if _is_capable_provider():
            # ── Phase B: structured extraction → per-section rewrite ──────────
            structured_data = _extract_resume_structured(resume_text)

            if structured_data:
                # Rewrite every section with JD context from the clean JSON
                structured_data = _rewrite_structured_with_jd(
                    structured_data, jd_text, jd_kws, freq_scores, resume_text
                )
                # Use rewritten structured summary (better than generic summary_prompt).
                # Fall back to pre-Phase-B new_summary if Phase B produced nothing.
                phase_b_sum = structured_data.get("summary", "").strip()
                if len(phase_b_sum) >= 25:
                    new_summary = phase_b_sum
                else:
                    # Phase B summary empty/short — push pre-Phase-B summary into structured
                    # data so generate_word_from_structured renders it correctly.
                    if len(new_summary) >= 25:
                        structured_data["summary"] = new_summary
                # ── Dedup same-company entries (LLM splits sub-projects into separate entries) ──
                seen_companies: dict = {}
                deduped_exp = []
                for job in structured_data.get("experience", []):
                    key = (job.get("company", "")[:35].lower().strip())
                    if key and key in seen_companies:
                        # Merge bullets into the first occurrence
                        seen_companies[key]["bullets"].extend(job.get("bullets", []))
                    else:
                        seen_companies[key] = job
                        deduped_exp.append(job)
                structured_data["experience"] = deduped_exp

                # Merge back — only overwrite a section if LLM returned COMPARABLE content.
                # Compare: LLM exp length vs original parsed exp length.
                # If LLM extraction is < 50% of original, it was cut off → keep original.
                orig_exp = sections.get("experience", "")
                exp_text = _exp_to_text(structured_data.get("experience", []))
                exp_is_complete = (
                    len(exp_text.strip()) > 200
                    and len(exp_text.strip()) >= len(orig_exp.strip()) * 0.5
                )
                if exp_is_complete:
                    sections["experience"] = exp_text
                # else: keep original sections["experience"] — LLM extraction was incomplete

                proj_text = _proj_to_text(structured_data.get("projects", []))
                if len(proj_text.strip()) > 40:
                    sections["projects"] = proj_text

                edu_text = _edu_to_text(structured_data.get("education", []))
                if len(edu_text.strip()) > 10:
                    sections["education"] = edu_text

                if structured_data.get("certifications"):
                    sections["certifications"] = "\n".join(structured_data["certifications"])
                if structured_data.get("skills"):
                    sk = structured_data["skills"]
                    if isinstance(sk, dict):
                        # Categorized dict — render as "Category: skill1, skill2"
                        lines = []
                        for cat, items in sk.items():
                            if items:
                                lines.append(f"{cat}: {', '.join(str(s) for s in items)}")
                        sections["skills"] = "\n".join(lines)
                    elif isinstance(sk, list):
                        sections["skills"] = "\n".join(f"- {s}" for s in sk)
                for k in ("name", "role", "email", "mobile", "location", "linkedin"):
                    if structured_data.get(k):
                        sections[k] = structured_data[k]
                structured_json_out = structured_data
                rephrased_count = 0
                existing_lower = {s.lower() for s in structured_data.get("skills", [])}
                added_skills = len([k for k in important_missing if k.lower() not in existing_lower])
                proj_lower = sections.get("projects", "").lower()
                groups = _group_by_domain(tech_missing[:12])
                new_proj_blocks = [
                    "[DRAFT - Review & update with actual candidate project]\n" + block
                    for domain, kws in [(d, k) for d, k in groups.items() if d != "Other Tools" and len(k) >= 2][:2]
                    if not any(kw in proj_lower for kw in kws)
                    for block in [_add_project_for_tech(domain, kws[:4])]
                    if block
                ]
                if new_proj_blocks:
                    sections["projects"] = (sections.get("projects", "") + "\n\n" + "\n\n".join(new_proj_blocks)).strip()

            else:
                # Fallback: per-block text rewrite (structured extraction failed)
                all_jd_freq = _score_keywords_by_jd_freq(jd_text, jd_kws)

                if sections.get("projects"):
                    proj_blocks = _split_into_blocks(sections["projects"])
                    sections["projects"] = "\n\n".join(
                        _rewrite_project_with_jd(
                            b,
                            _find_relevant_jd_kws(b, jd_kws, all_jd_freq),
                            _truncate(jd_text, 250),
                        )
                        for b in proj_blocks
                    )

                if sections.get("experience"):
                    job_blocks = _split_into_blocks(sections["experience"])
                    sections["experience"] = "\n\n".join(
                        _rewrite_job_block_with_jd(b, _find_relevant_jd_kws(b, jd_kws, all_jd_freq))
                        for b in job_blocks
                    )

                rephrased_count = 0
                if soft_gaps_list:
                    if sections.get("experience"):
                        sections["experience"], cnt = _rephrase_soft_gaps(sections["experience"], soft_gaps_list)
                        rephrased_count += cnt
                    if sections.get("projects"):
                        sections["projects"], cnt = _rephrase_soft_gaps(sections["projects"], soft_gaps_list)
                        rephrased_count += cnt

                sections["skills"] = _inject_skills(sections.get("skills", ""), tech_missing)
                non_tech_important = [w for w in hard_missing[:12] if not _is_tech_keyword(w)]
                if non_tech_important:
                    sections["skills"] += f"\nKey Competencies: {', '.join(non_tech_important)}"
                added_skills = len([k for k in important_missing if k.lower() not in res_kws])

                proj_lower = sections.get("projects", "").lower()
                groups = _group_by_domain(tech_missing[:12])
                new_proj_blocks = [
                    "[DRAFT - Review & update with actual candidate project]\n" + block
                    for domain, kws in [(d, k) for d, k in groups.items() if d != "Other Tools" and len(k) >= 2][:2]
                    if not any(kw in proj_lower for kw in kws)
                    for block in [_add_project_for_tech(domain, kws[:4])]
                    if block
                ]
                if new_proj_blocks:
                    sections["projects"] = (sections.get("projects", "") + "\n\n" + "\n\n".join(new_proj_blocks)).strip()

        else:
            # ── Programmatic path (Ollama / small models) ────────────────────
            cooccur = _build_jd_cooccurrence(jd_text, jd_kws)

            sections["skills"] = _inject_skills(sections.get("skills", ""), tech_missing)
            non_tech_important = [w for w in hard_missing[:12] if not _is_tech_keyword(w)]
            if non_tech_important:
                sections["skills"] += f"\nKey Competencies: {', '.join(non_tech_important)}"
            added_skills = len([k for k in important_missing if k.lower() not in res_kws])

            if sections.get("experience"):
                sections["experience"] = _reorder_bullets_by_relevance(sections["experience"], jd_kws)

            if sections.get("experience"):
                sections["experience"] = _enhance_experience_with_cooccurrence(
                    sections["experience"], hard_missing[:18], jd_kws, cooccur,
                )

            if sections.get("projects"):
                sections["projects"] = _enhance_experience_with_cooccurrence(
                    sections["projects"], hard_missing[:15], jd_kws, cooccur,
                )

            rephrased_count = 0
            if soft_gaps_list:
                if sections.get("experience"):
                    sections["experience"], cnt = _rephrase_soft_gaps(sections["experience"], soft_gaps_list)
                    rephrased_count += cnt
                if sections.get("projects"):
                    sections["projects"], cnt = _rephrase_soft_gaps(sections["projects"], soft_gaps_list)
                    rephrased_count += cnt

            groups = _group_by_domain(tech_missing[:12])
            new_proj_blocks = []
            for domain, kws in [(d, k) for d, k in groups.items() if d != "Other Tools" and len(k) >= 2][:2]:
                block = _add_project_for_tech(domain, kws[:4])
                if block:
                    new_proj_blocks.append("[DRAFT - Review & update with actual candidate project]\n" + block)
            if new_proj_blocks:
                sections["projects"] = (sections.get("projects", "") + "\n\n" + "\n\n".join(new_proj_blocks)).strip()

            still_missing = [w for w in tech_missing if w not in _extract_keywords(
                sections.get("experience", "") + " " + sections.get("projects", "")
            )]
            if still_missing[:4]:
                new_bullets = _augment_experience_bullets(still_missing[:4])
                if new_bullets:
                    sections["experience"] = (sections.get("experience", "") + "\n" + new_bullets).strip()

        # ── Summary enrichment (both paths) ───────────────────────────────────
        if new_summary and important_missing:
            top_absent = [w for w in important_missing[:4] if w not in new_summary.lower()][:2]
            if top_absent:
                new_summary = new_summary.rstrip('.')
                new_summary += f", with expertise in {' and '.join(top_absent)}."

        # ── Assemble structured draft ──────────────────────────────────────────
        draft = _build_structured_draft(sections, new_summary)

        if not draft.strip():
            return {"success": False, "error": "Could not build draft from resume"}

        # ATS keyword score (consistent with analyze_resume)
        draft_kws  = _extract_keywords(draft)
        new_score  = round(len(jd_kws & draft_kws) / max(len(jd_kws), 1) * 100)
        orig_score = round(len(jd_kws & res_kws)   / max(len(jd_kws), 1) * 100)

        # Semantic score after alignment
        sem_score_after = _semantic_score(draft, jd_text)

        # Role compatibility
        candidate_fn = detect_function(resume_text)
        jd_fn        = detect_function(jd_text)
        compat       = check_role_compatibility(candidate_fn, jd_fn)

        return {
            "success":                True,
            "draft":                  draft,
            "added_skills":           added_skills,
            "added_projects":         len(new_proj_blocks),
            "score_before":           orig_score,
            "score_after":            new_score,
            "sem_score_before":       round(sem_score_before, 1) if sem_score_before is not None else None,
            "sem_score_after":        round(sem_score_after, 1) if sem_score_after is not None else None,
            "rephrased_count":        rephrased_count,
            "soft_gap_count":         len(soft_gaps_list),
            "structured_resume_json": json.dumps(structured_json_out) if structured_json_out else None,
            "candidate_function":     candidate_fn,
            "jd_function":            jd_fn,
            "role_compatibility":     compat,
            "detected_role":          sections.get("role", ""),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
