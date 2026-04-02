# Architecture and process flow diagrams

These diagrams illustrate the major capabilities of the toolkit. All diagrams use [Mermaid](https://mermaid.js.org/) and render natively on GitHub.

---

## 1. Migration path overview

High-level choice map: which data path to use and what components are involved.

```mermaid
flowchart TD
    OS["Amazon OpenSearch Service\n(source cluster)"]

    subgraph PATH_A["Path A — Remote Reindex (Elastic Hosted only)"]
        direction LR
        ALB["ALB + SigV4 Proxy\n(if VPC-only domain)"]
        ER["POST _reindex\n(Kibana Dev Tools)"]
        ALB -->|"HTTPS + Basic Auth"| ER
    end

    subgraph PATH_B["Path B — Logstash"]
        direction LR
        LS["Logstash\n(logstash-input-opensearch)"]
    end

    subgraph PATH_C["Path C — Kafka buffer (optional)"]
        direction LR
        PROD["Kafka Producer\n(custom harvester)"]
        BROKER["Kafka Broker\n(topics)"]
        CONS["Kafka Consumer\n(Logstash or custom)"]
        PROD --> BROKER --> CONS
    end

    EC["Elastic Cloud\n(destination)"]

    OS -->|"direct / public endpoint"| PATH_A
    OS -->|"VPC-only domain"| ALB
    OS --> PATH_B
    OS --> PROD

    ER -->|"bulk write"| EC
    LS -->|"elasticsearch output"| EC
    CONS -->|"bulk write"| EC

    style PATH_A fill:#e8f4f8,stroke:#2196f3
    style PATH_B fill:#e8f8e8,stroke:#4caf50
    style PATH_C fill:#fff8e1,stroke:#ff9800
```

---

## 2. VPC proxy — request flow

Used when the OpenSearch domain has no public endpoint. The proxy runs inside AWS, signs requests with SigV4, and forwards them.

```mermaid
sequenceDiagram
    participant Client as Client<br/>(Elastic Cloud / Logstash)
    participant ALB as ALB<br/>(HTTPS :443, TLS termination)
    participant Proxy as Proxy / app.py<br/>(Flask + Gunicorn, HTTP :9200)
    participant OS as OpenSearch VPC Endpoint<br/>(HTTPS :443)
    participant IAM as AWS IAM / STS

    Client->>ALB: HTTPS request + Basic Auth
    ALB->>Proxy: HTTP forward (Basic Auth consumed)
    Proxy->>Proxy: _check_proxy_auth()
    Proxy->>IAM: get_credentials() via instance/task role
    IAM-->>Proxy: access_key, secret_key, session_token
    Proxy->>Proxy: AWS4Auth.sign(request)
    Proxy->>OS: HTTPS + SigV4 Authorization header
    OS-->>Proxy: response
    Proxy-->>ALB: filtered response headers
    ALB-->>Client: HTTPS response

    Note over Proxy,OS: GET /health → 200 OK (no SigV4, for ALB health checks)
```

---

## 3. Validation and preflight — tool flow

How `preflight.py` and `validate_migration.py` interact with both clusters.

```mermaid
flowchart LR
    CLI(["Operator / CI"])

    subgraph PREFLIGHT["preflight.py"]
        PF1["Ping source\n(GET /)"]
        PF2["Ping destination\n(GET /)"]
        PF3["HEAD source index\n(optional)"]
        PF4["HEAD dest index\n(optional)"]
        PF5["_count equality\n(--check-counts)"]
        PF1 --> PF2 --> PF3 --> PF4 --> PF5
    end

    subgraph VALIDATE["validate_migration.py"]
        V1["validate_index_name()"]
        V2["HEAD both indices\n(--check-existence)"]
        V3["GET _count\n(source + dest)"]
        V4["POST _search\n(sample IDs)\nmode: head / random /\nstratified / time_stratified"]
        V5["POST _mget\n(verify IDs on dest)"]
        V6["Output: text / json / csv\nExit: 0/1/2/3"]
        V1 --> V2 --> V3 --> V4 --> V5 --> V6
    end

    OS["OpenSearch\n(source)"]
    EC["Elastic Cloud\n(destination)"]

    CLI -->|"run before migration"| PREFLIGHT
    CLI -->|"run after migration"| VALIDATE

    PF1 & PF3 & PF5 -->|"SigV4 or Basic Auth"| OS
    PF2 & PF4 & PF5 -->|"API key or Basic Auth"| EC

    V2 & V3 & V4 -->|"SigV4 or Basic Auth"| OS
    V2 & V3 & V5 -->|"API key or Basic Auth"| EC

    V6 -->|"PASS / FAIL"| CLI
```

---

## 4. Multi-index batch migration — end-to-end process

Full lifecycle for migrating multiple indices, from generation through validation.

```mermaid
flowchart TD
    START(["Start: indices list\n(--indices / --indices-file)"])

    MIR["multi_index_reindex.py\n--format devtools / list\n--large / --mask-credentials"]

    subgraph REINDEX_LOOP["For each index"]
        direction TB
        DEVTOOLS["Paste into\nKibana Dev Tools\n(devtools format)"]
        ASYNC["POST _reindex?\nwait_for_completion=false"]
        POLL["poll_reindex_task.py\n--task-id <id>\n--json-progress"]
        DEVTOOLS --> ASYNC --> POLL
    end

    subgraph LOGSTASH_LOOP["Or: Logstash loop\n(list format)"]
        direction TB
        LSCMD["docker compose up\n(one run per index)"]
    end

    VALIDATE["validate_migration.py\n--indices-file indices.txt\n--check-existence\n--sample-size N\n--sample-mode stratified"]

    RESULT{{"All PASS?"}}
    CUTOVER(["Cutover:\nswitch reads to Elastic"])
    RETRY(["Investigate + re-run\nfailed indices"])

    START --> MIR
    MIR -->|"devtools"| REINDEX_LOOP
    MIR -->|"list"| LOGSTASH_LOOP
    REINDEX_LOOP --> VALIDATE
    LOGSTASH_LOOP --> VALIDATE
    VALIDATE --> RESULT
    RESULT -->|"Yes"| CUTOVER
    RESULT -->|"No"| RETRY
    RETRY --> VALIDATE
```

---

## 5. Infrastructure — AWS deployment topology

How the proxy, Logstash, and tooling fit into a typical AWS VPC layout.

```mermaid
flowchart TB
    subgraph INTERNET["Internet / Elastic Cloud"]
        EC["Elastic Cloud\n(Hosted or Serverless)"]
        OPS["Operator workstation\n(CLI tools)"]
    end

    subgraph AWS["AWS Account"]
        subgraph VPC["VPC"]
            subgraph PUBLIC["Public Subnet"]
                ALB["Application Load Balancer\nHTTPS :443 + ACM cert\nWAF (optional)"]
            end

            subgraph PRIVATE["Private Subnet"]
                ECS["ECS Task / EC2\nProxy/app.py (Gunicorn)\nTask Role → SigV4"]
                LS_HOST["ECS Task / EC2\nLogstash\n(logstash-input-opensearch)"]
            end

            subgraph OPENSEARCH_VPC["OpenSearch VPC Endpoint"]
                OS_EP["Amazon OpenSearch Service\n(VPC endpoint, no public access)"]
            end
        end

        SM["AWS Secrets Manager\n(credentials)"]
        IAM["IAM Role\nes:ESHttpGet/Post/Put/Head"]
    end

    EC -->|"HTTPS _reindex\nBasic Auth"| ALB
    OPS -->|"HTTPS preflight /\nvalidate_migration"| ALB
    ALB -->|"HTTP :9200"| ECS
    ECS -->|"HTTPS + SigV4"| OS_EP
    LS_HOST -->|"HTTP :9200\n(proxy path)"| ECS
    LS_HOST -->|"elasticsearch output\nbulk HTTPS"| EC
    ECS -.->|"AssumeRole"| IAM
    LS_HOST -.->|"secrets inject"| SM

    style PUBLIC fill:#fff3e0,stroke:#ff9800
    style PRIVATE fill:#e8f5e9,stroke:#4caf50
    style OPENSEARCH_VPC fill:#e3f2fd,stroke:#2196f3
    style INTERNET fill:#f3e5f5,stroke:#9c27b0
```

---

## 6. CI/CD pipeline

How the GitHub Actions workflow validates the toolkit on every push.

```mermaid
flowchart LR
    PUSH(["git push\nmain / PR"])

    subgraph CI["GitHub Actions — CI workflow"]
        direction TB

        subgraph MATRIX["test (Python 3.9 + 3.11)"]
            INSTALL["pip install\nrequirements + requirements-dev"]
            RUFF_CHECK["ruff check ."]
            RUFF_FMT["ruff format --check ."]
            MYPY["mypy"]
            PYTEST["pytest -q"]
            AUDIT["pip-audit\n(informational, non-blocking)"]
            INSTALL --> RUFF_CHECK --> RUFF_FMT --> MYPY --> PYTEST --> AUDIT
        end

        subgraph SECRETS["gitleaks (non-blocking)"]
            GL["gitleaks detect\n(full history)"]
        end

        subgraph TF["terraform-validate"]
            TF_FMT["terraform fmt -check"]
            TF_ALB["terraform init + validate\niac/terraform/proxy-alb"]
            TF_ECS["terraform init + validate\niac/terraform/proxy-ecs"]
            TF_FMT --> TF_ALB --> TF_ECS
        end
    end

    RESULT_OK(["All checks pass\n✓ merge / deploy"])
    RESULT_FAIL(["Fix and re-push"])

    PUSH --> CI
    MATRIX & SECRETS & TF --> RESULT_OK
    MATRIX -->|"failure"| RESULT_FAIL
```
