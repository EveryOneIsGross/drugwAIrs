## drugwAIrs

`drugwAIrs` is a **multi-agent economic simulation** where llm-driven autonomous agents compete in a shared, finite resource market. it's essentially drugwars (the classic ti-83 game) but instead of human players making buy/sell/travel decisions, you've got language models reasoning through strategy, risk, and survival.

![terminal](assets\terminal.png)

### the architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    WORLD STATE                              │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                     │
│  │ market  │  │ stock   │  │ events  │  (boom/bust/police) │
│  │ prices  │  │ depth   │  │ queue   │                     │
│  └────┬────┘  └────┬────┘  └────┬────┘                     │
│       └───────────┴───────────┴──────────────┐             │
│                                              ▼             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              AGENT LOOP (per day)                    │  │
│  │  1. market update + random events                    │  │
│  │  2. world events (mugging/intel/found cash)          │  │
│  │  3. police encounters                                │  │
│  │  4. local chat phase (agents in same location)       │  │
│  │  5. action phase (multi-action turns)                │  │
│  │  6. reflection → memory update                       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### the ghist

1. **emergent economics** - agents don't just react to static prices. their buy/sell actions *move* the market. supply decreases when bought, increases when sold. price elasticity per drug type. boom/bust cycles. the market is a shared commons that agents collectively shape.

2. **multi-prompt persona architecture** - each agent has separate prompt templates for: decision-making, enforcement encounters, mugger encounters, reflection, and chat. the `neo.yaml` shows how you can craft distinct personalities with different risk tolerances.

3. **imperfect information + intel decay** - agents only know local prices. scouting and random intel provide noisy estimates of other locations. intel decays probabilistically, forcing continuous information gathering.

4. **social layer** - agents at the same location can chat. this creates potential for bluffing, coordination, or misdirection. the chat history persists per-location and feeds back into agent context.

5. **multi-action turns** - unlike classic drugwars where you do one thing then travel, agents can chain buy→sell→bank→heal→travel in a single day. the llm must reason about action sequencing and when to cut a turn short.

6. **reflection loop** - after each turn, agents analyze their actions and update their mental model. this creates a form of episodic memory that influences future decisions.

### the flow

```
day 1:
  market prices fluctuate (natural drift + boom/bust chance)
  for each alive agent:
    check loan status (goons hit if overdue)
    display status
    generate world event (mugging/intel/cash find)
    check police encounter (if stayed too long)
  run local chat phase (agents in same borough talk)
  for each alive agent:
    llm generates action sequence (buy/sell/travel/etc)
    execute actions in order
    llm reflects on outcomes
    update turn history
  day++
  repeat until max_days or all dead
```

---

## quick start

```bash
# install deps
pip install aiohttp openai anthropic google-genai pydantic rich colorama pillow pyyaml python-dotenv tiktoken

# set api keys in .env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
OLLAMA_API_BASE=http://localhost:11434

# run with two default agents
python drugwAIrs.py --api ollama --model gpt-oss:20b

# run with custom agent personas
python drugwAIrs.py --api openai --model gpt-4o --agents neo trinity morpheus

# debug mode (shows all llm context)
python drugwAIrs.py --api anthropic --show-context --turn-delay 2.0
```

## creating agents

create `<name>.yaml` in project root. see `neo.yaml` for full template.

```yaml
name: Trinity
persona: "Aggressive arbitrage hunter. High risk tolerance."
model: "gpt-4o"  # optional override

decision:
  system: |
    You are {agent_name}. Persona: {persona}
    Output JSON only...
  user: |
    State: {state}
    Prices: {prices}
    ...
```

prompts support these phases: `decision`, `enforcement`, `mugger`, `reflection`, `chat`

## architecture

| component | purpose |
|-----------|---------|
| `api_client.py` | multi-provider llm abstraction (openai/anthropic/gemini/ollama/vllm/openrouter) |
| `drugwAIrs.py` | game loop, market simulation, agent orchestration |
| `prompts.yaml` | default prompt templates |
| `<agent>.yaml` | per-agent persona + prompt overrides |

## market mechanics

- **price elasticity**: each drug responds differently to supply changes
- **boom/bust**: 18% chance per location/drug per day
- **stock depth**: finite supply that regenerates slowly
- **intel decay**: information about other locations degrades over time
- **police heat**: staying too long in one location increases encounter probability

## agent capabilities

| action | effect |
|--------|--------|
| buy/sell | trade drugs, move market |
| travel | change location ($10), mugger risk |
| bank | deposit/withdraw (safe from theft) |
| loan | borrow up to $5000, due in 30 days |
| scout | gather intel on 2 random locations |
| heal | restore hp ($20/hp) |
| buy_gun | self-defense ($800 each) |

## why it works

1. **structured output** - pydantic models enforce valid json from llms
2. **bounded context** - agents see: recent turns, local prices, intel, events, action limits
3. **action limits** - computed per-turn to prevent hallucinated illegal moves
4. **reflection loop** - agents analyze outcomes, building episodic memory
5. **social layer** - chat enables coordination/deception between co-located agents

## metrics

game tracks: action success rate, unique states visited, police encounters, jail time, chat volume, profit sparklines per drug.

## extending

- add new drugs in `GameConfig.drugs`
- add locations in `GameConfig.locations`
- tune market dynamics via `price_elasticity`, `boom_bust_chance`, etc.
- swap llm providers per-agent via yaml `api` field

## license

do what you want. it's a toy.
