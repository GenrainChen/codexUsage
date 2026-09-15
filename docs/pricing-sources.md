# 刊例价来源与计算口径

核实日期：2026-09-13。价格文件为项目根目录的 `prices.json`；单位为美元 / 每百万 token，采用当前公开 API Standard 短上下文价格。历史用量也套用这份当前价格表，因此展示的是可比较的刊例价估算，不能用来核对历史账单或 Codex 订阅扣费。

## 已核实报价

| 模型 | 普通输入 | 缓存读取 | 缓存写入 | 输出 | 来源 |
| --- | ---: | ---: | ---: | ---: | --- |
| GPT-6 Astra | 10 | 1 | 12.5 | 50 | [模型页](https://developers.openai.com/api/docs/models/gpt-6-astra) |
| GPT-5.6 Sol | 4 | 0.4 | 5 | 20 | [模型页](https://developers.openai.com/api/docs/models/gpt-5.6-sol) |
| GPT-5.6 Terra | 2 | 0.2 | 2.5 | 12 | [模型页](https://developers.openai.com/api/docs/models/gpt-5.6-terra) |
| GPT-5.6 Luna | 0.2 | 0.02 | 0.25 | 1.2 | [模型页](https://developers.openai.com/api/docs/models/gpt-5.6-luna) |
| GPT-5.5 | 5 | 0.5 | 按普通输入价，无额外写入费 | 30 | [模型页](https://developers.openai.com/api/docs/models/gpt-5.5) |

GPT-5.6 Sol 当前公开促销价至少持续到 2026-11-21。Astra / GPT-5.6 的四类单价也已用 [API 总定价表](https://developers.openai.com/api/docs/pricing) 交叉核对。

GPT-5.3-Codex-Spark 在 [Codex 定价页](https://learn.chatgpt.com/docs/pricing) 只列为 research preview，没有数字单价；`gpt-reserve` 的同名公开 API 单价未核实。这些模型不写入价格表，保留其 token 统计并显示价格未知，不将未知价当作零，也不套用名称相似的模型价格。`codex-auto-review` 按用户指定的统计范围完全排除，不参与 token、金额和模型明细；此设置不代表其官方收费政策。

## 输入、缓存读取与缓存写入

[官方 Prompt caching 文档](https://developers.openai.com/api/docs/guides/prompt-caching) 的计费示例明确把输入总量拆成三个互斥类别：普通输入、缓存读取、缓存写入。缓存写入价替代该部分 token 的普通输入价，不是重复加收整笔输入费。

```text
I = input_tokens
R = cached_input_tokens
W = cache_write_input_tokens
普通输入 U = I - R - W

美元估算 = (U × input + R × cached + W × cache_write + O × output) / 1,000,000
```

API 原始字段为 `usage.input_tokens_details.cached_tokens` 与 `cache_write_tokens`，本地 Codex 日志使用上面的扁平字段名。GPT-5.5 没有额外写缓存费，因此价格表明确将它的 `cache_write` 设为与普通输入相同的 5 美元。其他模型如果缺少写缓存价且存在写入用量，保持部分已知，不猜测回退价格。缺少旧日志字段与明确记录零值应在数据质量上区分。

本次仅抽取本机 `token_count` 的模型和数值元数据，没有输出对话。2026-09-13 的日志可见写缓存字段，但抽样时数值均为 0；数据采集代理的更大范围扫描也未发现非零值。因此，本机记录能确认字段存在和 `total_tokens = input_tokens + output_tokens` 的表现，不能单靠这些零值样本证明写缓存的包含关系；拆分公式依据上述官方定义。

推理输出是 `output_tokens` 的子集；不可再把 `reasoning_output_tokens` 加进总 token 或重复计价。数据异常导致 `R + W > I` 时应记录异常，不能生成负输入费用。

## 默认未应用的收费变化

- 长上下文：Astra / GPT-5.6 单次请求输入超过 272,000 token 时，输入、缓存读取及缓存写入价格为短上下文的 2 倍，输出为 1.5 倍。GPT-5.5 模型页另写有整段 session 的长上下文规则。当前便签统一展示标准短上下文等价估算，不自动加这些倍率。[Astra 模型页](https://developers.openai.com/api/docs/models/gpt-6-astra)、[GPT-5.5 模型页](https://developers.openai.com/api/docs/models/gpt-5.5)
- Fast / Priority：Astra / GPT-5.6 的 API Fast 按对应 Standard 价格的 2 倍计费，Batch / Flex 为 0.5 倍。Priority 已更名 Fast，API 可接受两种名称。便签默认不根据 Codex 的速度选项套用此倍率。[API 定价表](https://developers.openai.com/api/docs/pricing)、[Fast mode 文档](https://developers.openai.com/api/docs/guides/fast-mode)
- Codex 订阅额度、credits 与 API 美元有各自规则。例如 Codex Astra Fast credits 为 Standard 的 2.5 倍；不能拿这个倍率替代 API Fast 的 2 倍，也不能把剩余额度百分比换算成美元。[Codex 定价页](https://learn.chatgpt.com/docs/pricing)
- 不含地区处理、工具调用、图像、语音等单独费用。[API 定价表](https://developers.openai.com/api/docs/pricing)
