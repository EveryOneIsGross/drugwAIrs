import json, random, argparse, asyncio, os
from collections import defaultdict
from typing import Optional, Literal, Dict, List

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from pydantic import BaseModel, Field
import yaml

import api_client
from api_client import initialize_api_client, call_api

console = Console()
SHOW_CONTEXT = False

async def run_llm(prompt: str, system_prompt: str = "", model_override: Optional[str] = None, context: str = "") -> str:
    return await call_api(
        prompt=prompt,
        system_prompt=system_prompt,
        context=context,
        model_override=model_override,
    )

class DrugConfig(BaseModel):
    base_price: int
    bulk: int

class GameConfig(BaseModel):
    max_days: int = 356
    locations: List[str] = ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"]
    drugs: Dict[str, DrugConfig] = {
        "cocaine": DrugConfig(base_price=100, bulk=1),
        "heroin": DrugConfig(base_price=120, bulk=1),
        "meth": DrugConfig(base_price=90, bulk=1),
        "weed": DrugConfig(base_price=50, bulk=2),
        "ecstasy": DrugConfig(base_price=80, bulk=1),
    }
    location_modifiers: Dict[str, float] = {
        "Bronx": 0.9,
        "Brooklyn": 1.0,
        "Manhattan": 1.4,
        "Queens": 1.1,
        "Staten Island": 0.8,
    }
    base_inventory_capacity: int = 200
    max_loan_amount: int = 5000
    loan_duration: int = 30
    loan_interest_rate: float = 0.1
    max_safe_turns: int = 3
    base_police_chance: int = 5
    travel_cost: int = 10
    boom_bust_chance: float = 0.18
    boom_multiplier: float = 3.5
    bust_multiplier: float = 0.25
    recall_turns: int = 16
    base_health: int = 100
    mugging_chance_travel: float = 0.12
    gun_price: int = 800
    heal_cost_per_hp: int = 20
    intel_decay_prob: float = 0.2
    base_stock_per_drug: Dict[str, int] = {
        "cocaine": 150,
        "heroin": 120,
        "meth": 200,
        "weed": 600,
        "ecstasy": 300,
    }
    drug_regen_per_day: Dict[str, int] = {
        "cocaine": 4,
        "heroin": 3,
        "meth": 8,
        "weed": 40,
        "ecstasy": 15,
    }
    price_elasticity: Dict[str, float] = {
        "cocaine": 0.8,
        "heroin": 0.9,
        "meth": 0.6,
        "weed": 0.5,
        "ecstasy": 0.7,
    }
    turn_delay: float = 0.5

class LLMConfig(BaseModel):
    decision_model: str = "gpt-oss:20b"
    enforcement_model: str = "gpt-oss:20b"
    reflection_model: str = "gpt-oss:20b"
    chat_model: str = "gpt-oss:20b"
    max_retries: int = 3
    retry_delay: float = 2.0

GAME = GameConfig()
LLM = LLMConfig()

DEFAULT_PROMPTS = {}
try:
    with open("prompts.yaml", "r", encoding="utf-8") as f:
        DEFAULT_PROMPTS = yaml.safe_load(f) or {}
except FileNotFoundError:
    DEFAULT_PROMPTS = {}

def load_agent_prompts(basename: str) -> dict:
    base = dict(DEFAULT_PROMPTS)
    if not basename:
        return base
    filename = f"{basename}.yaml"
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for k, v in data.items():
            base[k] = v
    except FileNotFoundError:
        console.print(f"[red]Prompt file {filename} not found. Falling back to prompts.yaml[/red]")
    return base

class GameAction(BaseModel):
    action: Literal["buy","sell","travel","loan","repay","bank","scout","heal","buy_gun","quit"]
    drug_type: Optional[Literal["cocaine","heroin","meth","weed","ecstasy"]] = None
    amount: Optional[int] = Field(None, ge=1)
    location: Optional[Literal["Bronx","Brooklyn","Manhattan","Queens","Staten Island"]] = None
    sub_action: Optional[Literal["deposit","withdraw"]] = None
    class Config:
        extra = "ignore"
        validate_assignment = True

class TurnDecision(BaseModel):
    actions: List[GameAction]
    class Config:
        extra = "ignore"
        validate_assignment = True

class MuggerDecision(BaseModel):
    decision: Literal["comply","fight","run"]
    class Config:
        extra = "ignore"

class ChatMessage(BaseModel):
    message: str = ""
    class Config:
        extra = "ignore"

class PlayerState(BaseModel):
    cash: int = 1000
    debt: int = 0
    loan_due_date: Optional[int] = None
    inventory: Dict[str,int] = Field(default_factory=lambda:{drug:0 for drug in GAME.drugs})
    location: str = Field(default_factory=lambda:random.choice(GAME.locations))
    bank: int = 0
    jail_time: int = 0
    turns_in_location: int = 0
    inventory_capacity: int = GAME.base_inventory_capacity
    health: int = GAME.base_health
    max_health: int = GAME.base_health
    guns: int = 0
    intel: Dict[str,Dict[str,int]] = Field(default_factory=dict)

class Agent(BaseModel):
    id: str
    name: str
    persona: str = "Neutral profit-seeking drug trader."
    api: Optional[str] = None
    model: Optional[str] = None
    state: PlayerState = Field(default_factory=PlayerState)
    prompts: dict = Field(default_factory=dict)
    turn_history: List[dict] = Field(default_factory=list)
    last_event: Optional[str] = None

class WorldState(BaseModel):
    day: int = 1
    agents: List[Agent] = Field(default_factory=list)
    global_turn_history: List[dict] = Field(default_factory=list)

world = WorldState()
location_chat_history: Dict[str, List[str]] = {}

class MarketStock(BaseModel):
    quantity: int
    regen_per_day: int

market_state: Dict[str, Dict[str, int]] = {
    loc: {drug: int(cfg.base_price * GAME.location_modifiers[loc])
          for drug, cfg in GAME.drugs.items()}
    for loc in GAME.locations
}

market_stock: Dict[str, Dict[str, MarketStock]] = {
    loc: {
        drug: MarketStock(
            quantity=int(GAME.base_stock_per_drug[drug] * random.uniform(0.8, 1.2)),
            regen_per_day=GAME.drug_regen_per_day[drug],
        )
        for drug in GAME.drugs.keys()
    }
    for loc in GAME.locations
}

class ActionReflection(BaseModel):
    result: str
    next_moves: list[str] = Field(default_factory=list)

class GameMetrics:
    def __init__(self):
        self.action_counts = defaultdict(int)
        self.successful_actions = 0
        self.failed_actions = 0
        self.visited_states = set()
        self.price_history = defaultdict(list)
        self.profit_history = []
        self.encounters = 0
        self.jail_time_served = 0
        self.agent_actions = defaultdict(lambda: defaultdict(int))
        self.chat_messages = 0

    def record_action(self, agent_id, action, result):
        self.action_counts[action] += 1
        self.agent_actions[agent_id][action] += 1
        if any(x in result for x in ["Invalid", "Not enough", "insufficient", "Insufficient"]):
            self.failed_actions += 1
        else:
            self.successful_actions += 1

    def record_state(self, agent: Agent):
        st = agent.state
        snap = (agent.id, st.cash, st.debt, st.location, tuple(sorted(st.inventory.items())))
        self.visited_states.add(snap)

    def record_prices(self):
        for d in GAME.drugs.keys():
            vals = [market_state[loc][d] for loc in GAME.locations]
            self.price_history[d].append(int(sum(vals)/len(vals)))

    def record_profit(self, prev_total, cur_total):
        self.profit_history.append(cur_total - prev_total)

    def record_encounter(self):
        self.encounters += 1

    def record_jail(self, days):
        self.jail_time_served += days

    def record_chat(self):
        self.chat_messages += 1

    def get_sparklines(self, window: int = 20) -> str:
        blocks = "▁▂▃▄▅▆▇█"
        lines = []
        for d, series in self.price_history.items():
            if len(series) < 2:
                continue
            chunk = series[-window:]
            lo, hi = min(chunk), max(chunk)
            if hi == lo:
                bars = "─" * len(chunk)
            else:
                span = hi - lo
                bars = "".join(blocks[int((p - lo) / span * (len(blocks) - 1))] for p in chunk)
            lines.append(f"{d}: {bars} (${chunk[-1]})")
        return "\n".join(lines)

    def get_stats_table(self):
        t = Table(title="Game Statistics", style="cyan")
        t.add_column("Metric", style="magenta")
        t.add_column("Value", style="green")
        total_actions = sum(self.action_counts.values())
        t.add_row("Total Actions", str(total_actions))
        t.add_row("Success Rate", f"{(self.successful_actions/total_actions*100):.1f}%" if total_actions else "0%")
        t.add_row("Unique Agent-States", str(len(self.visited_states)))
        t.add_row("Police Encounters", str(self.encounters))
        t.add_row("Jail Time Served", f"{self.jail_time_served} days")
        t.add_row("Chat Messages", str(self.chat_messages))
        if self.profit_history:
            total_profit = sum(self.profit_history)
            avg_profit = total_profit / len(self.profit_history)
            t.add_row("Total Profit (global)", f"${total_profit}")
            t.add_row("Average Profit/Turn", f"${avg_profit:.2f}")
        return t

game_metrics = GameMetrics()

def fmt(agent: Agent, tmpl: str, **extra) -> str:
    if not tmpl:
        return ""
    ctx = {"agent_name": agent.name, "persona": agent.persona}
    ctx.update(extra)
    return tmpl.format(**ctx)

def get_alive_agents() -> List[Agent]:
    return [a for a in world.agents if a.state.health > 0]

def get_agents_at_location(location: str) -> List[Agent]:
    return [a for a in get_alive_agents() if a.state.location == location]

def current_prices(agent: Agent) -> Dict[str, int]:
    return market_state[agent.state.location]

def local_stock(agent: Agent) -> Dict[str, int]:
    loc = agent.state.location
    return {d: market_stock[loc][d].quantity for d in GAME.drugs.keys()}

def inventory_used(st: PlayerState) -> int:
    return sum(GAME.drugs[d].bulk * q for d, q in st.inventory.items())

def display_status(agent: Agent):
    st = agent.state
    t = Table(title=f"{agent.name} - Day {world.day}", style="cyan")
    t.add_column("Attribute", style="magenta")
    t.add_column("Value", style="green")
    t.add_row("Cash", f"${st.cash}")
    t.add_row("Debt", f"${st.debt}")
    t.add_row("Bank", f"${st.bank}")
    t.add_row("Location", st.location)
    inv = ", ".join([f"{d}: {q}" for d, q in st.inventory.items() if q > 0]) or "Empty"
    t.add_row("Inventory", inv)
    t.add_row("Health", f"{st.health}/{st.max_health}")
    t.add_row("Guns", str(st.guns))
    t.add_row("Inv Space", f"{inventory_used(st)}/{st.inventory_capacity}")
    if st.debt > 0 and st.loan_due_date:
        t.add_row("Loan Due", f"Day {st.loan_due_date}")
    console.print(t)
    pt = Table(title=f"Local Prices @ {st.location}", style="yellow")
    pt.add_column("Drug", style="magenta")
    pt.add_column("Price", style="green")
    pt.add_column("Stock", style="cyan")
    prices = current_prices(agent)
    stocks = local_stock(agent)
    for d in GAME.drugs.keys():
        pt.add_row(d.capitalize(), f"${prices[d]}", str(stocks[d]))
    console.print(pt)
    if st.intel:
        it = Table(title=f"{agent.name} Intel (Other Locations)", style="blue")
        it.add_column("Location")
        it.add_column("Intel")
        for loc, drugs in st.intel.items():
            line = ", ".join(f"{d}: ~${price}" for d, price in drugs.items())
            it.add_row(loc, line)
        console.print(it)

def update_market_and_events() -> List[str]:
    events = []
    for loc in GAME.locations:
        for drug, cfg in GAME.drugs.items():
            stock = market_stock[loc][drug]
            stock.quantity += stock.regen_per_day
            natural_price = int(cfg.base_price * GAME.location_modifiers[loc])
            current_price = market_state[loc][drug]
            diff = natural_price - current_price
            current_price += int(diff * 0.25)
            current_price = max(5, current_price + random.randint(-10, 10))
            equilibrium = GAME.base_stock_per_drug[drug]
            q = max(1, stock.quantity)
            ratio = equilibrium / q
            elasticity = GAME.price_elasticity[drug]
            current_price = max(5, int(current_price * (ratio ** elasticity)))
            if random.random() < GAME.boom_bust_chance:
                is_boom = random.random() < 0.5
                if is_boom:
                    potential_price = int(current_price * GAME.boom_multiplier)
                    hard_cap = natural_price * 6
                    if potential_price < hard_cap:
                        current_price = potential_price
                        events.append(f"BOOM in {loc}: {drug} prices have exploded!")
                else:
                    potential_price = int(current_price * GAME.bust_multiplier)
                    hard_floor = max(5, int(natural_price * 0.1))
                    if current_price > hard_floor:
                        current_price = max(hard_floor, potential_price)
                        events.append(f"BUST in {loc}: {drug} prices have crashed!")
            market_state[loc][drug] = current_price
    for agent in world.agents:
        st = agent.state
        if st.intel:
            to_clear = [loc for loc in list(st.intel.keys()) if random.random() < GAME.intel_decay_prob]
            for loc in to_clear:
                del st.intel[loc]
    game_metrics.record_prices()
    return events

async def run_local_chat(agents: List[Agent]) -> Dict[str, str]:
    if len(agents) < 2:
        return {}
    loc = agents[0].state.location
    recent = location_chat_history.get(loc, [])[-8:]
    messages = {}
    async def get_chat_message(agent: Agent) -> Optional[tuple]:
        if agent.state.jail_time > 0:
            return None
        others_info = []
        for a in agents:
            if a.id == agent.id:
                continue
            inv_snip = ", ".join(f"{d}:{q}" for d, q in a.state.inventory.items() if q > 0) or "empty"
            others_info.append(f"- {a.name}: cash ~${a.state.cash}, guns {a.state.guns}, inv [{inv_snip}]")
        others_str = "\n".join(others_info) if others_info else "(no one else visible)"
        recent_str = "\n".join(recent) if recent else "(silence)"
        chat_prompts = agent.prompts.get("chat") or DEFAULT_PROMPTS.get("chat", {})
        system_tmpl = chat_prompts.get("system", "You are {agent_name}, a street dealer. Speak briefly. Output JSON only.")
        user_tmpl = chat_prompts.get("user", "Location: {location}\nOthers:\n{others}\nRecent chat:\n{recent_chat}\nRespond with: {{\"message\": \"...\"}}")
        system_msg = fmt(agent, system_tmpl)
        user_msg = fmt(agent, user_tmpl, location=loc, others=others_str, recent_chat=recent_str)
        if SHOW_CONTEXT:
            console.print(Panel(f"[bold]CHAT CONTEXT - {agent.name}[/bold]\n\n{user_msg}", style="yellow"))
        try:
            resp = await run_llm(prompt=user_msg, system_prompt=system_msg, model_override=agent.model or LLM.chat_model)
            parsed = ChatMessage.model_validate_json(resp)
            msg = parsed.message.strip()
            if msg:
                return (agent.id, msg)
        except Exception as e:
            console.print(f"[dim red]chat error for {agent.name}: {e}[/dim red]")
        return None
    results = await asyncio.gather(*[get_chat_message(a) for a in agents])
    for r in results:
        if r:
            aid, msg = r
            messages[aid] = msg
            agent_name = next((a.name for a in agents if a.id == aid), "???")
            location_chat_history.setdefault(loc, []).append(f"{agent_name}: {msg}")
            game_metrics.record_chat()
    return messages

async def get_mugger_decision(agent: Agent, scenario: str) -> str:
    st = agent.state
    state_str = (
        f"Day: {world.day}, Agent: {agent.name}, Cash: ${st.cash}, Debt: ${st.debt}, "
        f"Bank: ${st.bank}, Location: {st.location}, Health: {st.health}/{st.max_health}, "
        f"Guns: {st.guns}, Inventory: {', '.join([f'{d}: {q}' for d, q in st.inventory.items() if q>0]) or 'Empty'}"
    )
    mug_prompts = agent.prompts.get("mugger") or DEFAULT_PROMPTS.get("mugger", {})
    system_tmpl = mug_prompts.get("system", "")
    user_tmpl = mug_prompts.get("user", "")
    system_msg = fmt(agent, system_tmpl)
    user_msg = fmt(agent, user_tmpl, state=state_str, scenario=scenario)
    if SHOW_CONTEXT:
        console.print(Panel(f"[bold]MUGGER CONTEXT - {agent.name}[/bold]\n\n{user_msg}", style="red"))
    resp = await run_llm(prompt=user_msg, system_prompt=system_msg, model_override=agent.model or LLM.enforcement_model)
    return MuggerDecision.model_validate_json(resp).decision

async def handle_mugger_encounter(agent: Agent, scenario: str) -> str:
    st = agent.state
    if st.cash <= 0 and st.guns <= 0:
        dmg = random.randint(3, 10)
        st.health = max(0, st.health - dmg)
        return f"{agent.name}: a mugger {scenario}, but you were broke; you still lost {dmg} health."
    decision = await get_mugger_decision(agent, scenario)
    if decision == "comply":
        lost_cash = max(10, int(st.cash * random.uniform(0.1, 0.35)))
        lost_cash = min(lost_cash, st.cash)
        dmg = random.randint(0, 8)
        st.cash -= lost_cash
        st.health = max(0, st.health - dmg)
        return f"{agent.name}: complied with the mugger {scenario}, lost ${lost_cash} and {dmg} health."
    if decision == "fight":
        if st.guns <= 0:
            dmg = random.randint(15, 30)
            lost_cash = max(10, int(st.cash * random.uniform(0.2, 0.5)))
            lost_cash = min(lost_cash, st.cash)
            st.cash -= lost_cash
            st.health = max(0, st.health - dmg)
            return f"{agent.name}: tried to fight unarmed {scenario}, lost ${lost_cash} and {dmg} health."
        win_chance = 0.5 + 0.1 * min(st.guns, 3)
        if random.random() < win_chance:
            spent = random.randint(1, min(3, st.guns))
            st.guns -= spent
            dmg = random.randint(0, 10)
            st.health = max(0, st.health - dmg)
            return f"{agent.name}: fought off the mugger {scenario}, used {spent} gun(s), took {dmg} damage."
        dmg = random.randint(20, 40)
        lost_cash = max(10, int(st.cash * random.uniform(0.25, 0.6)))
        lost_cash = min(lost_cash, st.cash)
        st.cash -= lost_cash
        st.health = max(0, st.health - dmg)
        return f"{agent.name}: the fight went badly {scenario}, lost ${lost_cash} and {dmg} health."
    if decision == "run":
        run_chance = 0.65
        if random.random() < run_chance:
            dmg = random.randint(0, 10)
            lost_cash = max(0, int(st.cash * random.uniform(0.0, 0.15)))
            lost_cash = min(lost_cash, st.cash)
            st.cash -= lost_cash
            st.health = max(0, st.health - dmg)
            return f"{agent.name}: ran from the mugger {scenario}, lost ${lost_cash} and {dmg} health."
        dmg = random.randint(10, 25)
        lost_cash = max(10, int(st.cash * random.uniform(0.15, 0.4)))
        lost_cash = min(lost_cash, st.cash)
        st.cash -= lost_cash
        st.health = max(0, st.health - dmg)
        return f"{agent.name}: failed to escape the mugger {scenario}, lost ${lost_cash} and {dmg} health."
    dmg = random.randint(10, 20)
    lost_cash = max(10, int(st.cash * random.uniform(0.2, 0.4)))
    lost_cash = min(lost_cash, st.cash)
    st.cash -= lost_cash
    st.health = max(0, st.health - dmg)
    return f"{agent.name}: hesitated with a mugger {scenario}, lost ${lost_cash} and {dmg} health."

async def generate_world_event(agent: Agent) -> str:
    st = agent.state
    r = random.random()
    if r < 0.2:
        found = random.randint(50, 200)
        st.cash += found
        return f"{agent.name}: found a hidden stash worth ${found}."
    if r < 0.35:
        return await handle_mugger_encounter(agent, "while walking the streets")
    if r < 0.6:
        other_locs = [l for l in GAME.locations if l != st.location]
        loc = random.choice(other_locs)
        drug = random.choice(list(GAME.drugs.keys()))
        true_price = market_state[loc][drug]
        noise = random.randint(-int(true_price * 0.15), int(true_price * 0.15))
        approx = max(5, true_price + noise)
        intel = st.intel.get(loc, {})
        intel[drug] = approx
        st.intel[loc] = intel
        return f"{agent.name}: rumor – {drug} is trading around ${approx} in {loc}."
    return f"{agent.name}: nothing unusual happened today."

def compute_action_limits(agent: Agent):
    st = agent.state
    prices = current_prices(agent)
    free_space = max(0, st.inventory_capacity - inventory_used(st))
    buy_limits = {}
    loc = st.location
    for d, cfg in GAME.drugs.items():
        max_by_cash = st.cash // prices[d] if prices[d] > 0 else 0
        max_by_space = free_space // cfg.bulk if cfg.bulk > 0 else 0
        max_by_supply = market_stock[loc][d].quantity
        buy_limits[d] = max(0, min(max_by_cash, max_by_space, max_by_supply))
    sell_limits = {d: max(0, q) for d, q in st.inventory.items()}
    bank_deposit_max = max(0, st.cash)
    bank_withdraw_max = max(0, st.bank)
    missing_hp = max(0, st.max_health - st.health)
    max_heal_by_cash = st.cash // GAME.heal_cost_per_hp
    heal_max = max(0, min(missing_hp, max_heal_by_cash))
    can_take_loan = st.debt == 0
    loan_max = GAME.max_loan_amount if can_take_loan else 0
    repay_max = min(st.cash, st.debt + int(st.debt * GAME.loan_interest_rate)) if st.debt > 0 else 0
    return {
        "buy_limits": buy_limits,
        "sell_limits": sell_limits,
        "bank_deposit_max": bank_deposit_max,
        "bank_withdraw_max": bank_withdraw_max,
        "heal_max": heal_max,
        "loan_max": loan_max,
        "repay_max": repay_max,
    }

def update_loan_status(agent: Agent) -> Optional[str]:
    st = agent.state
    if st.loan_due_date and world.day >= st.loan_due_date:
        penalty = int(st.debt * 0.5)
        damage = random.randint(10, 30)
        st.debt += penalty
        st.loan_due_date += GAME.loan_duration
        st.health = max(0, st.health - damage)
        return f"{agent.name}: loan overdue! Goons hit you for {damage} HP and add ${penalty} to your debt."
    return None

def update_turn_history(agent: Agent, action, result, state_snapshot, prices, event=None):
    entry = {"day": world.day, "agent_id": agent.id, "agent_name": agent.name, "action": action, "result": result, "state": state_snapshot, "prices": prices, "event": event}
    agent.turn_history.append(entry)
    if len(agent.turn_history) > GAME.recall_turns:
        agent.turn_history.pop(0)
    world.global_turn_history.append(entry)
    max_global = GAME.recall_turns * max(1, len(world.agents))
    if len(world.global_turn_history) > max_global:
        world.global_turn_history.pop(0)

async def get_law_enforcement_decision(agent: Agent, options):
    class EnforcementDecision(BaseModel):
        decision: Literal["pay_fine","lose_inventory","go_to_jail","bribe","fight"]
    try:
        st = agent.state
        state_str = (
            f"Day: {world.day}, Agent: {agent.name}, Cash: ${st.cash}, "
            f"Debt: ${st.debt}, Bank: ${st.bank}, Location: {st.location}, "
            f"Health: {st.health}/{st.max_health}, Guns: {st.guns}, "
            f"Inventory: {', '.join([f'{d}: {q}' for d, q in st.inventory.items() if q>0]) or 'Empty'}"
        )
        options_str = "\n".join([f"{k}: {v}" for k, v in options.items()])
        enf_prompts = agent.prompts.get("enforcement") or DEFAULT_PROMPTS.get("enforcement", {})
        system_tmpl = enf_prompts.get("system", "")
        user_tmpl = enf_prompts.get("user", "")
        system_msg = fmt(agent, system_tmpl)
        user_msg = fmt(agent, user_tmpl, state=state_str, options=options_str)
        if SHOW_CONTEXT:
            console.print(Panel(f"[bold]ENFORCEMENT CONTEXT - {agent.name}[/bold]\n\n{user_msg}", style="red"))
        resp = await run_llm(prompt=user_msg, system_prompt=system_msg, model_override=agent.model or LLM.enforcement_model)
        return EnforcementDecision.model_validate_json(resp).decision
    except Exception as e:
        console.print(f"[red]Error getting law enforcement decision for {agent.name}: {e}[/red]")
        return "go_to_jail"

async def handle_law_enforcement_options(agent: Agent) -> str:
    st = agent.state
    fine = random.randint(150, 600)
    bribe_amount = 500
    options = {
        "pay_fine": f"Pay a fine of ${fine}. Lose cash, avoid further trouble.",
        "lose_inventory": "Lose all units of a random drug, keep cash and freedom.",
        "go_to_jail": "Go to jail for 1-3 days. Keep cash and inventory, lose time.",
        "bribe": f"Attempt to bribe the cops for ${bribe_amount}. Might fail if you lack cash.",
        "fight": "Use guns to fight. High risk to health; success avoids other penalties.",
    }
    decision = await get_law_enforcement_decision(agent, options)
    if decision == "pay_fine":
        st.cash = max(0, st.cash - fine)
        return f"{agent.name}: paid a fine of ${fine}."
    if decision == "lose_inventory":
        if any(st.inventory.values()):
            drug = random.choice([d for d, q in st.inventory.items() if q > 0])
            lost = st.inventory[drug]
            st.inventory[drug] = 0
            return f"{agent.name}: cops confiscated {lost} units of {drug}."
        st.cash = max(0, st.cash - fine)
        return f"{agent.name}: no inventory to seize. Paid a fine of ${fine}."
    if decision == "go_to_jail":
        days = random.randint(1, 3)
        st.jail_time = days
        game_metrics.record_jail(days)
        return f"{agent.name}: sent to jail for {days} day(s)."
    if decision == "bribe":
        if st.cash >= bribe_amount:
            st.cash -= bribe_amount
            return f"{agent.name}: successfully bribed the cops for ${bribe_amount}."
        st.jail_time = 1
        game_metrics.record_jail(1)
        return f"{agent.name}: bribe failed due to low cash. You spend 1 day in jail."
    if decision == "fight":
        if st.guns <= 0:
            dmg = random.randint(15, 35)
            st.health = max(0, st.health - dmg)
            st.jail_time = 1
            game_metrics.record_jail(1)
            return f"{agent.name}: tried to fight unarmed. Lost {dmg} health and spent a day in jail."
        win_chance = 0.5 + 0.1 * min(st.guns, 3)
        if random.random() < win_chance:
            spent_bullets = random.randint(1, min(3, st.guns))
            st.guns -= spent_bullets
            return f"{agent.name}: fought off the cops using {spent_bullets} gun(s). No further penalty."
        dmg = random.randint(20, 40)
        st.health = max(0, st.health - dmg)
        st.jail_time = 2
        game_metrics.record_jail(2)
        return f"{agent.name}: the shootout went badly. Lost {dmg} health and spent 2 days in jail."
    st.jail_time = 1
    game_metrics.record_jail(1)
    return f"{agent.name}: confusing response. You end up in jail for 1 day."

async def law_enforcement_encounter(agent: Agent) -> Optional[str]:
    st = agent.state
    st.turns_in_location += 1
    if st.turns_in_location <= GAME.max_safe_turns:
        return None
    loc = st.location
    total_stock = sum(market_stock[loc][d].quantity for d in GAME.drugs.keys())
    base_total = sum(GAME.base_stock_per_drug[d] for d in GAME.drugs.keys())
    ratio = total_stock / base_total if base_total > 0 else 1.0
    dyn_chance = int(GAME.base_police_chance * ratio)
    dyn_chance = max(1, min(95, dyn_chance))
    if random.randint(1, 100) <= dyn_chance:
        return await handle_law_enforcement_options(agent)
    return None

def build_other_agents_summary(agent: Agent) -> str:
    parts = []
    for other in world.agents:
        if other.id == agent.id:
            continue
        st = other.state
        if st.health <= 0:
            status = "OUT (dead)"
        else:
            status = f"at {st.location}, cash ~${st.cash}, guns {st.guns}"
        inv_snip = ", ".join(f"{d}:{q}" for d, q in st.inventory.items() if q > 0)
        if inv_snip:
            status += f", inv [{inv_snip}]"
        parts.append(f"- {other.name}: {status}")
    if not parts:
        return "No other active agents."
    return "Other agents in the world:\n" + "\n".join(parts)

def build_local_chat_context(agent: Agent) -> str:
    loc = agent.state.location
    recent = location_chat_history.get(loc, [])[-6:]
    if not recent:
        return ""
    return "Recent local chatter:\n" + "\n".join(recent)

async def get_user_actions(agent: Agent) -> List[dict]:
    attempt = 0
    while attempt < LLM.max_retries:
        try:
            st = agent.state
            state_str = (
                f"Day: {world.day}, Agent: {agent.name}, Cash: ${st.cash}, Debt: ${st.debt}, "
                f"Bank: ${st.bank}, Location: {st.location}, "
                f"Health: {st.health}/{st.max_health}, Guns: {st.guns}, "
                f"Inventory: {', '.join([f'{d}: {q}' for d, q in st.inventory.items() if q>0]) or 'Empty'}"
            )
            prices = current_prices(agent)
            prices_str = ", ".join([f"{d}: ${p}" for d, p in prices.items()])
            intel_str = ""
            if st.intel:
                intel_str = "Known intel on other locations:\n" + "\n".join(
                    f"{loc}: " + ", ".join(f"{d}: ~${v}" for d, v in drugs.items())
                    for loc, drugs in st.intel.items()
                )
            depth = local_stock(agent)
            depth_str = "Local market depth (approx units available): " + ", ".join(f"{d}: {q}" for d, q in depth.items())
            others_str = build_other_agents_summary(agent)
            chat_context = build_local_chat_context(agent)
            event_str = ""
            if agent.last_event:
                event_str = f"Recent events for you:\n{agent.last_event}\n"
            if chat_context:
                event_str += f"\n{chat_context}\n"
            recall_str = ""
            if agent.turn_history:
                recall_str = "Recent Turns:\n"
                for turn in agent.turn_history:
                    recall_str += (
                        f"Day {turn['day']} [{turn['agent_name']}]: "
                        f"Action={turn['action']}, Result={turn['result']}, "
                        f"State={turn['state']}, Prices={turn['prices']}, "
                        f"Event={turn['event'] or 'None'}\n"
                    )
            limits = compute_action_limits(agent)
            buy_limits_str = ", ".join(f"{d}: up to {n}" for d, n in limits["buy_limits"].items())
            sell_limits_str = ", ".join(f"{d}: up to {n}" for d, n in limits["sell_limits"].items() if n > 0) or "none (no inventory)"
            bank_str = f"Deposit: 1..{limits['bank_deposit_max']} (if >0), Withdraw: 1..{limits['bank_withdraw_max']} (if >0)"
            heal_str = f"Heal HP: 1..{limits['heal_max']} (if >0, {GAME.heal_cost_per_hp} cash per HP)"
            loan_str = f"Loan: allowed up to {limits['loan_max']} (only if debt == 0). Repay: 1..{limits['repay_max']} (if debt > 0)."
            history_str = game_metrics.get_sparklines()
            dec_prompts = agent.prompts.get("decision") or DEFAULT_PROMPTS.get("decision", {})
            system_tmpl = dec_prompts.get("system", "")
            user_tmpl = dec_prompts.get("user", "")
            system_msg = fmt(agent, system_tmpl)
            schema_hint = """
You are allowed to take MULTIPLE actions this day, like the original Drugwars.

Return a SINGLE JSON object of the form:
{
  "actions": [
    {
      "action": one of ["buy","sell","travel","loan","repay","bank","scout","heal","buy_gun","quit"],
      "drug_type": one of ["cocaine","heroin","meth","weed","ecstasy"] or null,
      "amount": positive integer or null,
      "location": one of ["Bronx","Brooklyn","Manhattan","Queens","Staten Island"] or null,
      "sub_action": one of ["deposit","withdraw"] or null
    },
    ...
  ]
}

Rules:
- Execute actions IN ORDER.
- "travel" ends the day: do NOT include any actions after a travel.
- You may chain multiple buy/sell/bank/loan/repay/heal/buy_gun/scout actions before travel.
- Do not include more than 6 actions in one day.
- If you only want one action, still wrap it as a single-element actions list.
- Do NOT include any keys other than "actions" at the top level.
"""
            user_msg = fmt(
                agent,
                user_tmpl,
                recall=recall_str,
                state=state_str + "\n" + depth_str + "\n" + others_str,
                prices=prices_str,
                intel=intel_str or "",
                event=event_str,
                buy_limits=buy_limits_str,
                sell_limits=sell_limits_str,
                bank_limits=bank_str,
                heal_limits=heal_str,
                loan_limits=loan_str,
                price_history=history_str,
            ) + "\n\n" + schema_hint
            if SHOW_CONTEXT:
                console.print(Panel(f"[bold]AGENT CONTEXT - {agent.name}[/bold]\n\n{user_msg}", style="magenta"))
            resp = await run_llm(prompt=user_msg, system_prompt=system_msg, model_override=agent.model or LLM.decision_model)
            actions: List[dict] = []
            try:
                parsed = TurnDecision.model_validate_json(resp)
                actions = [a.model_dump(exclude_none=True) for a in parsed.actions]
            except Exception:
                single = GameAction.model_validate_json(resp)
                actions = [single.model_dump(exclude_none=True)]
            cleaned = []
            for a in actions:
                if "amount" in a and a["amount"] is not None and a["amount"] < 1:
                    continue
                cleaned.append(a)
            if not cleaned:
                raise ValueError("No valid actions in turn decision")
            return cleaned
        except Exception as e:
            console.print(f"[red]Error getting action(s) for {agent.name}: {e}[/red]")
            attempt += 1
            await asyncio.sleep(LLM.retry_delay)
    return []

async def process_action(agent: Agent, action_data) -> str:
    st = agent.state
    if st.jail_time > 0:
        msg = f"{agent.name}: in jail for {st.jail_time} more days. Cannot act."
        st.jail_time -= 1
        return msg
    act = action_data.get("action")
    prices = current_prices(agent)
    loc = st.location
    if act == "buy":
        drug = action_data.get("drug_type")
        amt = action_data.get("amount", 0)
        if not drug or drug not in GAME.drugs:
            return f"{agent.name}: invalid or missing drug type."
        if not isinstance(amt, int) or amt < 1:
            return f"{agent.name}: invalid amount."
        bulk_needed = GAME.drugs[drug].bulk * amt
        if inventory_used(st) + bulk_needed > st.inventory_capacity:
            return f"{agent.name}: not enough inventory space."
        if amt > market_stock[loc][drug].quantity:
            return f"{agent.name}: not enough supply of {drug} in {loc}."
        cost = prices[drug] * amt
        if st.cash < cost:
            return f"{agent.name}: insufficient funds to buy."
        st.cash -= cost
        st.inventory[drug] += amt
        market_stock[loc][drug].quantity -= amt
        return f"{agent.name}: bought {amt} {drug} for ${cost}."
    if act == "sell":
        drug = action_data.get("drug_type")
        amt = action_data.get("amount", 0)
        if not drug or drug not in GAME.drugs:
            return f"{agent.name}: invalid or missing drug type."
        if not isinstance(amt, int) or amt < 1:
            return f"{agent.name}: invalid amount."
        if st.inventory.get(drug, 0) < amt:
            return f"{agent.name}: not enough {drug} to sell."
        revenue = prices[drug] * amt
        st.inventory[drug] -= amt
        st.cash += revenue
        market_stock[loc][drug].quantity += amt
        return f"{agent.name}: sold {amt} {drug} for ${revenue}."
    if act == "travel":
        new_loc = action_data.get("location")
        if not new_loc or new_loc not in GAME.locations:
            return f"{agent.name}: invalid or missing location."
        if new_loc == loc:
            return f"{agent.name}: already in {loc}."
        if st.cash < GAME.travel_cost:
            return f"{agent.name}: insufficient funds to travel."
        st.cash -= GAME.travel_cost
        st.location = new_loc
        st.turns_in_location = 0
        if random.random() < GAME.mugging_chance_travel:
            return await handle_mugger_encounter(agent, f"while traveling to {new_loc}")
        return f"{agent.name}: traveled to {new_loc} for ${GAME.travel_cost}."
    if act == "loan":
        amt = action_data.get("amount", 0)
        if not isinstance(amt, int) or amt < 1:
            return f"{agent.name}: invalid loan amount."
        if amt > GAME.max_loan_amount:
            return f"{agent.name}: loan exceeds maximum of ${GAME.max_loan_amount}."
        if st.debt > 0:
            return f"{agent.name}: already has outstanding loan."
        st.cash += amt
        st.debt = amt
        st.loan_due_date = world.day + GAME.loan_duration
        total_due = amt + int(amt * GAME.loan_interest_rate)
        return f"{agent.name}: borrowed ${amt}. Repay ${total_due} by day {st.loan_due_date}."
    if act == "repay":
        amt = action_data.get("amount", 0)
        if not isinstance(amt, int) or amt < 1:
            return f"{agent.name}: invalid repayment amount."
        if st.debt <= 0:
            return f"{agent.name}: no outstanding debt."
        total_due = st.debt + int(st.debt * GAME.loan_interest_rate)
        payment = min(amt, total_due, st.cash)
        st.cash -= payment
        st.debt -= payment
        if st.debt <= 0:
            st.debt = 0
            st.loan_due_date = None
            return f"{agent.name}: loan fully repaid (${payment})."
        return f"{agent.name}: repaid ${payment}. Remaining principal: ${st.debt}."
    if act == "bank":
        sub = action_data.get("sub_action")
        amt = action_data.get("amount", 0)
        if sub == "deposit":
            if not isinstance(amt, int) or amt < 1 or amt > st.cash:
                return f"{agent.name}: invalid deposit amount."
            st.cash -= amt
            st.bank += amt
            return f"{agent.name}: deposited ${amt}."
        if sub == "withdraw":
            if not isinstance(amt, int) or amt < 1 or amt > st.bank:
                return f"{agent.name}: invalid withdrawal amount."
            st.bank -= amt
            st.cash += amt
            return f"{agent.name}: withdrew ${amt}."
        return f"{agent.name}: invalid or missing bank sub_action."
    if act == "scout":
        other_locs = [l for l in GAME.locations if l != st.location]
        if not other_locs:
            return f"{agent.name}: nowhere else to scout."
        k = min(2, len(other_locs))
        chosen = random.sample(other_locs, k=k)
        msg_parts = []
        for dest in chosen:
            intel = {}
            for drug in GAME.drugs.keys():
                true_price = market_state[dest][drug]
                noise = random.randint(-int(true_price * 0.15), int(true_price * 0.15))
                approx = max(5, true_price + noise)
                intel[drug] = approx
            st.intel[dest] = intel
            msg_parts.append(f"{dest}: " + ", ".join(f"{d} ~${p}" for d, p in intel.items()))
        return f"{agent.name}: scouted and gathered intel:\n" + "\n".join(msg_parts)
    if act == "heal":
        if st.health >= st.max_health:
            return f"{agent.name}: already at full health."
        missing = st.max_health - st.health
        desired = action_data.get("amount", missing)
        if not isinstance(desired, int) or desired < 1:
            return f"{agent.name}: invalid heal amount."
        heal_hp = min(desired, missing)
        cost = heal_hp * GAME.heal_cost_per_hp
        if st.cash < cost:
            affordable = st.cash // GAME.heal_cost_per_hp
            if affordable <= 0:
                return f"{agent.name}: needs ${GAME.heal_cost_per_hp} to heal 1 HP. Broke."
            heal_hp = affordable
            cost = heal_hp * GAME.heal_cost_per_hp
        st.cash -= cost
        st.health += heal_hp
        return f"{agent.name}: healed {heal_hp} HP for ${cost}."
    if act == "buy_gun":
        amt = action_data.get("amount", 1)
        if not isinstance(amt, int) or amt < 1:
            return f"{agent.name}: invalid gun purchase amount."
        cost = GAME.gun_price * amt
        if st.cash < cost:
            return f"{agent.name}: insufficient funds. Guns cost ${GAME.gun_price} each."
        st.cash -= cost
        st.guns += amt
        return f"{agent.name}: purchased {amt} gun(s) for ${cost}."
    if act == "quit":
        return f"{agent.name}: has chosen to quit the game."
    return f"{agent.name}: unknown action."

async def get_action_reflection(agent: Agent, actions_taken: List[dict], results: List[str], previous_state, current_state) -> Optional[str]:
    try:
        ref_prompts = agent.prompts.get("reflection") or DEFAULT_PROMPTS.get("reflection", {})
        system_tmpl = ref_prompts.get("system", "")
        user_tmpl = ref_prompts.get("user", "")
        system_msg = fmt(agent, system_tmpl)
        actions_str = "\n".join(f"{i+1}. {a['action']} → {r}" for i, (a, r) in enumerate(zip(actions_taken, results)))
        user_msg = fmt(
            agent,
            user_tmpl,
            previous_state=previous_state,
            actions=actions_str,
            results=actions_str,
            current_state=current_state,
        )
        if SHOW_CONTEXT:
            console.print(Panel(f"[bold]REFLECTION CONTEXT - {agent.name}[/bold]\n\n{user_msg}", style="blue"))
        resp = await run_llm(prompt=user_msg, system_prompt=system_msg, model_override=agent.model or LLM.reflection_model)
        r = ActionReflection.model_validate_json(resp)
        parts = [f"RESULT: {r.result}"]
        if r.next_moves:
            parts.append("\n".join(r.next_moves))
        return "\n".join(parts)
    except Exception as e:
        console.print(f"[red]Error getting action reflection for {agent.name}: {e}[/red]")
        return None

async def run_chat_phase(alive_agents: List[Agent]) -> Dict[str, Dict[str, str]]:
    location_messages: Dict[str, Dict[str, str]] = {}
    locations_seen = set()
    for agent in alive_agents:
        loc = agent.state.location
        if loc in locations_seen:
            continue
        locations_seen.add(loc)
        cohort = get_agents_at_location(loc)
        if len(cohort) < 2:
            continue
        console.print(f"[yellow]── local chat @ {loc} ({len(cohort)} agents) ──[/yellow]")
        messages = await run_local_chat(cohort)
        location_messages[loc] = messages
        for aid, msg in messages.items():
            speaker = next((a.name for a in cohort if a.id == aid), "???")
            console.print(f"[yellow]{speaker}:[/yellow] {msg}")
        for a in cohort:
            for aid, msg in messages.items():
                if aid != a.id:
                    speaker = next((x.name for x in cohort if x.id == aid), "???")
                    a.last_event = (a.last_event or "") + f"\n[chat] {speaker}: {msg}"
    return location_messages

async def process_agent_turn(agent: Agent, world_events: Dict, police_events: Dict):
    st = agent.state
    if st.health <= 0:
        return
    if st.jail_time > 0:
        console.print(f"[red]{agent.name} is in jail for {st.jail_time} more days.[/red]")
        st.jail_time -= 1
        return
    prev_state_for_reflection = {
        "cash": st.cash,
        "inventory": st.inventory.copy(),
        "location": st.location,
        "debt": st.debt,
        "bank": st.bank,
        "health": st.health,
    }
    prev_assets = st.cash + st.bank
    actions = await get_user_actions(agent)
    if not actions:
        console.print(f"[red]{agent.name}: no valid actions returned. Skipping.[/red]")
        return
    actions_taken = []
    results_collected = []
    for idx, action_data in enumerate(actions):
        if st.health <= 0:
            break
        if st.jail_time > 0 and idx > 0:
            break
        msg = await process_action(agent, action_data)
        actions_taken.append(action_data)
        results_collected.append(msg)
        game_metrics.record_action(agent.id, action_data["action"], msg)
        game_metrics.record_state(agent)
        cur_assets = st.cash + st.bank
        game_metrics.record_profit(prev_assets, cur_assets)
        prev_assets = cur_assets
        if msg:
            console.print(f"[green]{msg}[/green]")
            if "quit the game" in msg:
                st.health = 0
        if action_data["action"] in ["travel", "quit"]:
            break
        if st.health <= 0:
            break
    cur_state_for_reflection = {
        "cash": st.cash,
        "inventory": st.inventory.copy(),
        "location": st.location,
        "debt": st.debt,
        "bank": st.bank,
        "health": st.health,
    }
    if actions_taken:
        reflection = await get_action_reflection(agent, actions_taken, results_collected, prev_state_for_reflection, cur_state_for_reflection)
        if reflection:
            console.print(f"[cyan]{agent.name} reflection:\n{reflection}[/cyan]")
            agent.last_event = (agent.last_event or "") + "\nPrev action analysis:\n" + reflection
        last_action = actions_taken[-1]
        last_msg = results_collected[-1]
        state_snapshot = f"Cash: ${st.cash}, Debt: ${st.debt}, Location: {st.location}, Inventory: {st.inventory}, Health: {st.health}"
        prices_snapshot = ", ".join([f"{d}: ${p}" for d, p in current_prices(agent).items()])
        turn_event_summary_parts = []
        if world_events.get(agent.id):
            turn_event_summary_parts.append(world_events[agent.id])
        if police_events.get(agent.id):
            turn_event_summary_parts.append(police_events[agent.id])
        if reflection:
            turn_event_summary_parts.append("Prev action analysis:\n" + reflection)
        turn_event_summary = "\n".join(turn_event_summary_parts) if turn_event_summary_parts else None
        update_turn_history(agent, last_action["action"], last_msg, state_snapshot, prices_snapshot, turn_event_summary)

async def main(agent_tokens: List[str]):
    if not world.agents:
        if not agent_tokens:
            agent_tokens = ["agent1", "agent2"]
        for i, token in enumerate(agent_tokens, start=1):
            prompts = load_agent_prompts(token)
            display_name = prompts.get("name", token or f"Agent {i}")
            persona = prompts.get("persona", "Profit-driven but survival-aware drug trader.")
            model_override = prompts.get("model")
            api_override = prompts.get("api")
            world.agents.append(Agent(
                id=f"A{i}",
                name=display_name,
                persona=persona,
                api=api_override,
                model=model_override,
                prompts=prompts,
            ))
    console.print(Panel(
        f"[bold yellow]Welcome to Multi-Agent Drug Wars![/bold yellow]\n"
        f"{len(world.agents)} LLM traders share one finite commodity market.\n"
        "Survive, profit, and outplay the others.",
        style="green"
    ))
    while world.day <= GAME.max_days:
        alive_agents = get_alive_agents()
        if not alive_agents:
            console.print(Panel("[bold red]All agents are dead. Game Over.[/bold red]", style="red"))
            break
        console.print(Panel(f"[bold]DAY {world.day} — {len(alive_agents)} agents alive[/bold]", style="white"))
        market_events = update_market_and_events()
        if market_events:
            for ev in market_events:
                console.print(f"[magenta]{ev}[/magenta]")
        world_events = {}
        police_events = {}
        for agent in alive_agents:
            st = agent.state
            agent.last_event = None
            loan_msg = update_loan_status(agent)
            if loan_msg:
                console.print(f"[red]{loan_msg}[/red]")
                agent.last_event = (agent.last_event or "") + "\n" + loan_msg
            display_status(agent)
            w_ev = await generate_world_event(agent)
            world_events[agent.id] = w_ev
            console.print(f"[blue]{w_ev}[/blue]")
            agent.last_event = (agent.last_event or "") + "\n" + w_ev
            p_ev = await law_enforcement_encounter(agent)
            police_events[agent.id] = p_ev
            if p_ev:
                console.print(f"[red]{p_ev}[/red]")
                game_metrics.record_encounter()
                agent.last_event = (agent.last_event or "") + "\n" + p_ev
        await run_chat_phase(alive_agents)
        if world.day % 10 == 0:
            console.print(game_metrics.get_stats_table())
        for agent in alive_agents:
            await process_agent_turn(agent, world_events, police_events)
        world.day += 1
        await asyncio.sleep(GAME.turn_delay)
    console.print(Panel("[bold red]Game Over[/bold red]", style="red"))
    console.print(game_metrics.get_stats_table())
    for agent in world.agents:
        st = agent.state
        total_assets = st.cash + st.bank + sum(q * current_prices(agent)[d] for d, q in st.inventory.items()) - st.debt
        console.print(Panel(
            f"[bold]{agent.name} Summary[/bold]\n"
            f"Total Assets: ${total_assets}\n"
            f"Health: {st.health}\n"
            f"Location: {st.location}\n"
            f"Debt: ${st.debt}\n"
            f"Bank: ${st.bank}",
            style="white"
        ))

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--api", required=True, choices=["ollama","openai","anthropic","vllm","openrouter","gemini"])
    p.add_argument("--model", help="Override base LLM model for decision/enforcement/reflection/chat")
    p.add_argument("--show-context", action="store_true")
    p.add_argument("--agents", nargs="+", help="Agent config basenames (e.g. neo trinity morpheus)")
    p.add_argument("--turn-delay", type=float, default=0.5, help="Delay between turns in seconds")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    initialize_api_client(args)
    if args.model:
        LLM.decision_model = args.model
        LLM.enforcement_model = args.model
        LLM.reflection_model = args.model
        LLM.chat_model = args.model
    if args.show_context:
        SHOW_CONTEXT = True
        os.environ["LLM_SHOW_CONTEXT"] = "1"
        api_client.VERBOSE = True
    else:
        os.environ["LLM_SHOW_CONTEXT"] = "0"
        api_client.VERBOSE = False
    if args.turn_delay:
        GAME.turn_delay = args.turn_delay
    agent_tokens = args.agents or []
    asyncio.run(main(agent_tokens=agent_tokens))
