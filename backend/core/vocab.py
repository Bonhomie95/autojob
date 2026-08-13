"""
vocab.py — Curated skill / title vocabulary for offline CV and JD analysis.

This is the knowledge base that replaces LLM calls for CV parsing, job
discovery, and match scoring. Everything here is plain data — no API, no
model, no network.

Two structures:

  SKILLS  — canonical skill name → (category, aliases). Aliases are matched
            case-insensitively with word boundaries, so "node.js", "nodejs",
            and "node js" all resolve to the canonical "Node.js".

  TITLES  — canonical role title → adjacent roles. Used to expand a CV's
            observed titles into a realistic set of search queries: someone
            who has been a "Backend Engineer" should also see "Software
            Engineer" and "Platform Engineer" postings.

Adding a skill: put the canonical form as the key, list every spelling you
expect to see in the wild as aliases. Aliases must be lowercase.
"""

from __future__ import annotations

import re

# ──────────────────────────────────────────────────────────────
# Skills
#   canonical → (category, [aliases])
# Categories drive the CORE SKILLS section grouping in the generated CV.
# ──────────────────────────────────────────────────────────────

SKILLS: dict[str, tuple[str, list[str]]] = {
    # ── Languages ────────────────────────────────────────────
    "Python":       ("Languages", ["python", "python3", "py"]),
    "JavaScript":   ("Languages", ["javascript", "js", "ecmascript"]),
    "TypeScript":   ("Languages", ["typescript", "ts"]),
    "Go":           ("Languages", ["golang", "go lang"]),
    "Rust":         ("Languages", ["rust", "rustlang"]),
    "Java":         ("Languages", ["java"]),
    "Kotlin":       ("Languages", ["kotlin"]),
    "Swift":        ("Languages", ["swift", "swiftui"]),
    "C#":           ("Languages", ["c#", "csharp", "c sharp", ".net"]),
    "C++":          ("Languages", ["c++", "cpp", "cplusplus"]),
    "C":            ("Languages", ["c language", "ansi c"]),
    "Ruby":         ("Languages", ["ruby"]),
    "PHP":          ("Languages", ["php"]),
    "Scala":        ("Languages", ["scala"]),
    "Elixir":       ("Languages", ["elixir"]),
    "R":            ("Languages", ["r language", "rstats"]),
    "SQL":          ("Languages", ["sql"]),
    "Bash":         ("Languages", ["bash", "shell scripting", "shell script", "zsh"]),
    "Solidity":     ("Languages", ["solidity"]),

    # ── Frontend ─────────────────────────────────────────────
    "React":        ("Frontend", ["react", "react.js", "reactjs"]),
    "Next.js":      ("Frontend", ["next.js", "nextjs", "next js"]),
    "Vue.js":       ("Frontend", ["vue", "vue.js", "vuejs"]),
    "Nuxt":         ("Frontend", ["nuxt", "nuxt.js", "nuxtjs"]),
    "Angular":      ("Frontend", ["angular", "angularjs"]),
    "Svelte":       ("Frontend", ["svelte", "sveltekit"]),
    "Redux":        ("Frontend", ["redux", "redux toolkit", "rtk"]),
    "Tailwind CSS": ("Frontend", ["tailwind", "tailwind css", "tailwindcss"]),
    "HTML":         ("Frontend", ["html", "html5"]),
    "CSS":          ("Frontend", ["css", "css3", "sass", "scss", "less"]),
    "Webpack":      ("Frontend", ["webpack"]),
    "Vite":         ("Frontend", ["vite"]),
    "Accessibility": ("Frontend", ["accessibility", "a11y", "wcag"]),

    # ── Mobile ───────────────────────────────────────────────
    "React Native": ("Mobile", ["react native", "react-native"]),
    "Flutter":      ("Mobile", ["flutter", "dart"]),
    "iOS":          ("Mobile", ["ios", "uikit", "xcode"]),
    "Android":      ("Mobile", ["android", "jetpack compose"]),
    "Expo":         ("Mobile", ["expo"]),

    # ── Backend ──────────────────────────────────────────────
    "Node.js":      ("Backend", ["node", "node.js", "nodejs", "node js"]),
    "Express":      ("Backend", ["express", "express.js", "expressjs"]),
    "NestJS":       ("Backend", ["nest", "nestjs", "nest.js"]),
    "Django":       ("Backend", ["django"]),
    "Flask":        ("Backend", ["flask"]),
    "FastAPI":      ("Backend", ["fastapi", "fast api"]),
    "Spring":       ("Backend", ["spring", "spring boot", "springboot"]),
    "Rails":        ("Backend", ["rails", "ruby on rails"]),
    "Laravel":      ("Backend", ["laravel"]),
    "GraphQL":      ("Backend", ["graphql", "apollo"]),
    "REST API":     ("Backend", ["rest", "rest api", "restful", "restful api"]),
    "gRPC":         ("Backend", ["grpc", "protobuf", "protocol buffers"]),
    "WebSockets":   ("Backend", ["websocket", "websockets", "socket.io", "socketio"]),
    "Microservices": ("Backend", ["microservice", "microservices", "micro-services"]),
    "Celery":       ("Backend", ["celery"]),

    # ── Databases ────────────────────────────────────────────
    "PostgreSQL":   ("Databases", ["postgres", "postgresql", "psql"]),
    "MySQL":        ("Databases", ["mysql", "mariadb"]),
    "MongoDB":      ("Databases", ["mongo", "mongodb", "mongoose"]),
    "Redis":        ("Databases", ["redis"]),
    "SQLite":       ("Databases", ["sqlite", "sqlite3"]),
    "Elasticsearch": ("Databases", ["elasticsearch", "elastic search", "opensearch"]),
    "DynamoDB":     ("Databases", ["dynamodb", "dynamo db"]),
    "Cassandra":    ("Databases", ["cassandra"]),
    "Prisma":       ("Databases", ["prisma"]),
    "SQLAlchemy":   ("Databases", ["sqlalchemy"]),

    # ── DevOps / Cloud ───────────────────────────────────────
    "AWS":          ("DevOps", ["aws", "amazon web services", "ec2", "s3", "lambda",
                                "eks", "ecs", "cloudformation", "vpc", "rds"]),
    "Google Cloud": ("DevOps", ["gcp", "google cloud", "google cloud platform",
                                "bigquery", "cloud run"]),
    "Azure":        ("DevOps", ["azure", "microsoft azure"]),
    "Docker":       ("DevOps", ["docker", "dockerfile", "containerization",
                                "containerisation"]),
    "Kubernetes":   ("DevOps", ["kubernetes", "k8s", "helm", "kubectl"]),
    "Terraform":    ("DevOps", ["terraform"]),
    "Ansible":      ("DevOps", ["ansible"]),
    "CI/CD":        ("DevOps", ["ci/cd", "cicd", "ci cd", "continuous integration",
                                "continuous delivery", "continuous deployment"]),
    "GitHub Actions": ("DevOps", ["github actions", "gh actions"]),
    "Jenkins":      ("DevOps", ["jenkins"]),
    "GitLab CI":    ("DevOps", ["gitlab ci", "gitlab-ci"]),
    "ArgoCD":       ("DevOps", ["argocd", "argo cd"]),
    "Nginx":        ("DevOps", ["nginx"]),
    "Linux":        ("DevOps", ["linux", "ubuntu", "debian", "centos"]),
    "Prometheus":   ("DevOps", ["prometheus"]),
    "Grafana":      ("DevOps", ["grafana"]),
    "Observability": ("DevOps", ["observability", "monitoring", "datadog",
                                 "opentelemetry", "sentry"]),
    "Kafka":        ("DevOps", ["kafka", "event streaming"]),
    "RabbitMQ":     ("DevOps", ["rabbitmq", "rabbit mq"]),
    "Serverless":   ("DevOps", ["serverless", "lambda functions"]),
    "IaC":          ("DevOps", ["infrastructure as code", "iac"]),
    "SRE":          ("DevOps", ["sre", "site reliability"]),

    # ── AI / ML ──────────────────────────────────────────────
    "Machine Learning": ("AI / ML", ["machine learning", "ml"]),
    "Deep Learning":    ("AI / ML", ["deep learning", "neural network",
                                     "neural networks"]),
    "PyTorch":      ("AI / ML", ["pytorch", "torch"]),
    "TensorFlow":   ("AI / ML", ["tensorflow", "keras"]),
    "LLMs":         ("AI / ML", ["llm", "llms", "large language model",
                                 "large language models", "gpt", "openai",
                                 "anthropic", "claude"]),
    "RAG":          ("AI / ML", ["rag", "retrieval augmented generation",
                                 "vector database", "embeddings", "pinecone",
                                 "weaviate", "chroma"]),
    "LangChain":    ("AI / ML", ["langchain", "llamaindex"]),
    "NLP":          ("AI / ML", ["nlp", "natural language processing"]),
    "Computer Vision": ("AI / ML", ["computer vision", "opencv"]),
    "Pandas":       ("AI / ML", ["pandas", "numpy", "scipy"]),
    "scikit-learn": ("AI / ML", ["scikit-learn", "sklearn", "scikit learn"]),
    "Spark":        ("AI / ML", ["spark", "pyspark", "databricks"]),
    "Airflow":      ("AI / ML", ["airflow", "dagster", "prefect"]),

    # ── Web3 ─────────────────────────────────────────────────
    "Ethereum":     ("Web3", ["ethereum", "evm", "erc20", "erc-20", "erc721"]),
    "Solana":       ("Web3", ["solana", "anchor"]),
    "Smart Contracts": ("Web3", ["smart contract", "smart contracts"]),
    "Web3.js":      ("Web3", ["web3.js", "web3js", "ethers.js", "ethers", "viem",
                              "wagmi"]),
    "Hardhat":      ("Web3", ["hardhat", "truffle", "foundry"]),
    "DeFi":         ("Web3", ["defi", "decentralized finance"]),
    "IPFS":         ("Web3", ["ipfs"]),
    "Blockchain":   ("Web3", ["blockchain", "web3"]),

    # ── Testing / Practices ──────────────────────────────────
    "Jest":         ("Tooling", ["jest", "vitest"]),
    "Pytest":       ("Tooling", ["pytest", "unittest"]),
    "Cypress":      ("Tooling", ["cypress", "playwright", "selenium"]),
    "TDD":          ("Tooling", ["tdd", "test driven", "test-driven"]),
    "Git":          ("Tooling", ["git", "github", "gitlab", "bitbucket"]),
    "Agile":        ("Tooling", ["agile", "scrum", "kanban", "sprint"]),
    "Jira":         ("Tooling", ["jira", "confluence", "linear"]),
    "Figma":        ("Tooling", ["figma"]),
    "Stripe":       ("Tooling", ["stripe", "payment integration", "paystack"]),
    "Firebase":     ("Tooling", ["firebase", "supabase"]),
}


# ──────────────────────────────────────────────────────────────
# Titles
#   canonical → adjacent titles worth searching for
# ──────────────────────────────────────────────────────────────

TITLES: dict[str, list[str]] = {
    "Software Engineer": [
        "Software Developer", "Backend Engineer", "Full Stack Engineer",
    ],
    "Software Developer": [
        "Software Engineer", "Full Stack Developer", "Backend Developer",
    ],
    "Full Stack Engineer": [
        "Full Stack Developer", "Software Engineer", "Backend Engineer",
        "Frontend Engineer",
    ],
    "Frontend Engineer": [
        "Frontend Developer", "React Developer", "UI Engineer",
        "Full Stack Engineer",
    ],
    "Backend Engineer": [
        "Backend Developer", "Software Engineer", "Platform Engineer",
        "API Engineer",
    ],
    "Mobile Engineer": [
        "Mobile Developer", "React Native Developer", "iOS Engineer",
        "Android Engineer",
    ],
    "React Native Developer": [
        "Mobile Engineer", "Mobile Developer", "React Developer",
    ],
    "DevOps Engineer": [
        "Site Reliability Engineer", "Platform Engineer", "Cloud Engineer",
        "Infrastructure Engineer",
    ],
    "Site Reliability Engineer": [
        "DevOps Engineer", "Platform Engineer", "Infrastructure Engineer",
    ],
    "Platform Engineer": [
        "DevOps Engineer", "Backend Engineer", "Infrastructure Engineer",
    ],
    "Data Engineer": [
        "Analytics Engineer", "Backend Engineer", "Data Platform Engineer",
    ],
    "Machine Learning Engineer": [
        "ML Engineer", "AI Engineer", "Data Scientist", "Research Engineer",
    ],
    "AI Engineer": [
        "Machine Learning Engineer", "ML Engineer", "LLM Engineer",
    ],
    "Data Scientist": [
        "Machine Learning Engineer", "Data Analyst", "Research Scientist",
    ],
    "Blockchain Engineer": [
        "Web3 Engineer", "Smart Contract Engineer", "Solidity Developer",
    ],
    "Web3 Engineer": [
        "Blockchain Engineer", "Smart Contract Engineer", "Full Stack Engineer",
    ],
    "QA Engineer": [
        "Test Engineer", "Automation Engineer", "SDET",
    ],
    "Security Engineer": [
        "Application Security Engineer", "DevSecOps Engineer",
    ],
}

# Words that mark a line as a job title rather than prose.
TITLE_TOKENS = {
    "engineer", "developer", "programmer", "architect", "scientist",
    "analyst", "consultant", "specialist", "administrator", "sdet",
}

# ──────────────────────────────────────────────────────────────
# Seniority
# ──────────────────────────────────────────────────────────────

SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "staff", "principal"]

SENIORITY_MARKERS: dict[str, list[str]] = {
    "intern":    ["intern", "internship", "trainee", "apprentice"],
    "junior":    ["junior", "jr", "entry level", "entry-level", "graduate",
                  "associate", "early career"],
    "mid":       ["mid level", "mid-level", "intermediate", "ii", "software engineer ii"],
    "senior":    ["senior", "sr", "senior level", "iii", "lead"],
    "staff":     ["staff", "principal engineer", "tech lead", "team lead"],
    "principal": ["principal", "distinguished", "architect", "head of",
                  "director", "vp of engineering", "chief"],
}

# Minimum realistic years of experience per seniority band. Used to reject
# postings the candidate has no realistic chance at (and ones far beneath them).
SENIORITY_MIN_YEARS: dict[str, float] = {
    "intern":    0.0,
    "junior":    0.0,
    "mid":       2.0,
    "senior":    4.0,
    "staff":     7.0,
    "principal": 9.0,
}


# ──────────────────────────────────────────────────────────────
# Compiled matchers
# ──────────────────────────────────────────────────────────────

def _build_alias_index() -> list[tuple[re.Pattern, str, str]]:
    """
    Compile one regex per alias, longest-first so "react native" wins over
    "react" and ".net" wins over "net".
    """
    entries: list[tuple[str, str, str]] = []
    for canonical, (category, aliases) in SKILLS.items():
        for alias in aliases:
            entries.append((alias, canonical, category))
    entries.sort(key=lambda e: len(e[0]), reverse=True)

    compiled: list[tuple[re.Pattern, str, str]] = []
    for alias, canonical, category in entries:
        # \b doesn't work around symbols like "c++" or "c#", so guard with
        # explicit non-word-adjacent lookarounds instead.
        escaped = re.escape(alias).replace(r"\ ", r"[\s\-_]+")
        pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
        compiled.append((re.compile(pattern, re.IGNORECASE), canonical, category))
    return compiled


_ALIAS_INDEX = _build_alias_index()


def match_skills(text: str) -> dict[str, str]:
    """
    Find every known skill mentioned in text.
    Returns {canonical skill: category}, preserving vocabulary order.
    """
    if not text:
        return {}
    lowered = text.lower()
    found: dict[str, str] = {}
    for pattern, canonical, category in _ALIAS_INDEX:
        if canonical in found:
            continue
        if pattern.search(lowered):
            found[canonical] = category
    return found


def skill_category(canonical: str) -> str:
    entry = SKILLS.get(canonical)
    return entry[0] if entry else "Tooling"


def adjacent_titles(title: str) -> list[str]:
    """Expand a title into itself plus its adjacent roles."""
    for canonical, neighbours in TITLES.items():
        if canonical.lower() == title.lower():
            return [canonical] + neighbours
    return [title]


def detect_seniority(text: str) -> str:
    """
    Return the highest seniority marker present in text, or "" if none.
    Highest wins so "Senior Staff Engineer" reads as staff, not senior.
    """
    if not text:
        return ""
    lowered = text.lower()
    best = ""
    for level in SENIORITY_ORDER:
        for marker in SENIORITY_MARKERS[level]:
            if re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", lowered):
                best = level
                break
    return best
