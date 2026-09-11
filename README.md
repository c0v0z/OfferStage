# OfferSage 求职助手

OfferSage 是一个面向应届生的本地 AI 求职助手。它会比较简历和职位描述，给出匹配度、缺口、带证据的行动建议，生成定制化简历 bullet points，并提供基础模拟面试。

## 快速启动

需要 Python 3.10 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000 。默认 `MOCK_LLM=true`，不需要 API Key 即可检查完整流程。Mock 模式用于本地验收，输出是确定性的；接入真实模型时编辑 `.env`：

```env
MOCK_LLM=false
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=你的_API_Key
LLM_MODEL=deepseek-chat
```

`LLM_BASE_URL` 需要指向 OpenAI 兼容接口的根地址，程序会自动请求 `/chat/completions`。API Key 只从环境变量读取，不要提交 `.env`。

## 经历检索与 Embedding

每次分析会自动完成一条 RAG 链路：结构化简历 -> 拆分项目/工作/教育/技能经历 -> 生成向量 -> 写入 SQLite -> 将职位要求作为查询检索 Top-K 经历 -> 把检索结果传给匹配分析和简历改写。

默认使用 `EMBEDDING_MODE=local`，通过确定性的本地 Hash Embedding 和关键词重排实现零配置检索。它适合本地演示和测试，不需要下载额外模型。接入兼容 `/embeddings` 接口的模型时，在 `.env` 中配置：

```env
EMBEDDING_MODE=api
EMBEDDING_BASE_URL=https://你的-embedding服务/v1
EMBEDDING_API_KEY=你的_embedding_key
EMBEDDING_MODEL=text-embedding-3-small
```

`EMBEDDING_MODE=auto` 会优先请求远程 Embedding，服务不可用时自动回退到本地向量。DeepSeek 的聊天模型 Key 可以继续用于聊天请求，但需确认你的 Embedding 服务确实提供 `/embeddings` 接口。

## 功能

- 上传或粘贴 TXT、PDF、DOCX 简历和职位描述
- 提取技能、项目、职位要求并计算匹配度
- 展示优势、缺口和高优先级行动建议
- 每条建议保留原始证据；没有证据的生成内容会标记为“需要确认”
- 根据目标职位生成中文或英文简历 bullet points
- 进行 5 题多轮模拟面试，提交回答后获得评分和追问
- SQLite 保存分析记录，刷新页面后可查看历史
- 候选人档案和独立经历库，可保存多份项目/工作/教育经历
- 可通过检索接口查看某个职位查询命中的经历和相关度
- DeepSeek 结构化输出失败时自动重试；匹配分析仍失败则使用本地规则兜底

## API

启动服务后可访问 http://127.0.0.1:8000/docs 查看 Swagger 文档。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/health` | 检查服务和模型配置 |
| POST | `/api/analyze` | 创建一次简历/JD 分析，支持 multipart 文本或文件 |
| GET | `/api/applications/{id}` | 读取一条分析记录 |
| POST | `/api/rewrite` | 生成定制化简历内容 |
| POST | `/api/interview/start` | 创建模拟面试 |
| POST | `/api/interview/{id}/answer` | 提交当前问题的回答 |
| GET | `/api/history` | 获取最近分析记录 |
| GET | `/api/profiles` | 查看候选人档案 |
| POST | `/api/profiles` | 创建候选人档案 |
| GET | `/api/profiles/{id}/experiences` | 查看档案中的独立经历 |
| POST | `/api/profiles/{id}/experiences` | 添加一条经历并建立向量 |
| GET | `/api/profiles/{id}/retrieve?query=...` | 按职位要求检索 Top-K 经历 |

分析结果中的 `analysis_source` 会说明来源：`deepseek` 表示首次成功，`deepseek_repaired` 表示第二次结构修复成功，`local_fallback` 表示模型两次都没有遵守结构后使用本地规则完成匹配。`warning` 会给出简短的用户可读提示。

## 测试

```powershell
pytest -q
```

测试使用 Mock LLM 和本地 Embedding，不需要网络或 API Key，覆盖文本、PDF、DOCX 解析、模型 JSON 重试、证据校验、档案/经历索引、向量检索、API 闭环和面试流程。

## 项目结构

```text
app/
  agent.py       Agent 工作流、证据校验和面试状态
  database.py    SQLite 持久化、档案、经历和向量表
  llm.py         OpenAI 兼容客户端和 Mock LLM
  retrieval.py   Embedding、向量索引和 Top-K 检索
  main.py        FastAPI 路由
  parsers.py     TXT/PDF/DOCX 文档解析
  schemas.py     请求和响应结构
static/
  index.html     单页工作区
  app.js         前端交互
  styles.css     工作区样式
tests/           单元和 API 测试
```

## 常见问题

- 页面提示“未配置 LLM_API_KEY”：确认 `.env` 存在，或者设置 `MOCK_LLM=true`。
- PDF 没有提取出文字：扫描版 PDF 没有文本层，需要先 OCR 后再上传。
- 模型返回格式错误：客户端会自动重试一次；如果仍失败，接口会返回明确错误，不会保存不完整的分析。
- 匹配分析结构连续错误：系统会保留简历解析、职位解析和 RAG 检索结果，使用本地规则生成完整匹配分析，页面会显示 `local_fallback` 提示。
- 需要清空本地数据：停止服务后删除项目根目录的 `offersage.db`，下次启动会自动创建。
