import openai
import json
import random
import os
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from jsonschema import validate, ValidationError

# Initialize Rich Console
console = Console()

# Configure OpenAI to use Ollama's API endpoint
openai.api_base = 'http://localhost:11434/v1'  # Ollama's default API endpoint
openai.api_key = 'ollama'  # Required by Ollama but typically unused

# Game Constants
MAX_DAYS = 356
LOCATIONS = ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"]
DRUG_TYPES = {
    "cocaine": {"base_price": 100},
    "heroin": {"base_price": 120},
    "meth": {"base_price": 90},
    "weed": {"base_price": 50},
    "ecstasy": {"base_price": 80}
}

MAX_LOAN_AMOUNT = 5000
LOAN_DURATION = 30  # days
LOAN_INTEREST_RATE = 0.1  # 10% interest

# Initialize Game State
game_state = {
    "day": 1,
    "cash": 1000,
    "debt": 0,
    "loan_due_date": None,
    "inventory": {drug: 0 for drug in DRUG_TYPES},
    "location": random.choice(LOCATIONS),
    "bank": 0,
    "jail_time": 0,
    "turn_history": []
}

# Initialize Drug Prices
drug_prices = {drug: info["base_price"] for drug, info in DRUG_TYPES.items()}

# Define JSON Schema for Validation
action_schema = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["buy", "sell", "travel", "loan", "repay", "bank", "quit"]
        },
        "drug_type": {
            "type": "string",
            "enum": ["cocaine", "heroin", "meth", "weed", "ecstasy"]
        },
        "amount": {"type": "integer", "minimum": 1},
        "location": {
            "type": "string",
            "enum": ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"]
        },
        "sub_action": {
            "type": "string",
            "enum": ["deposit", "withdraw"]
        }
    },
    "required": ["action"],
    "additionalProperties": False
}

def update_prices():
    """Update drug prices based on random fluctuations."""
    for drug in drug_prices:
        fluctuation = random.randint(-10, 10)
        drug_prices[drug] = max(10, drug_prices[drug] + fluctuation)

def display_status():
    """Display the current game status using Rich tables."""
    table = Table(title=f"Drug Wars - Day {game_state['day']}", style="cyan")
    table.add_column("Attribute", style="magenta")
    table.add_column("Value", style="green")
    table.add_row("Cash", f"${game_state['cash']}")
    table.add_row("Debt", f"${game_state['debt']}")
    table.add_row("Bank", f"${game_state['bank']}")
    table.add_row("Location", game_state['location'])
    inventory = ", ".join([f"{drug}: {qty}" for drug, qty in game_state['inventory'].items() if qty > 0]) or "Empty"
    table.add_row("Inventory", inventory)
    console.print(table)
    if game_state['debt'] > 0:
        table.add_row("Loan Due", f"Day {game_state['loan_due_date']}")
    # Display Drug Prices
    price_table = Table(title="Local Drug Prices", style="yellow")
    price_table.add_column("Drug", style="magenta")
    price_table.add_column("Price", style="green")
    for drug, price in drug_prices.items():
        price_table.add_row(drug.capitalize(), f"${price}")
    console.print(price_table)

def generate_random_event():
    """Generate a random event to introduce unpredictability."""
    events = [
        "You found a hidden stash in your inventory!",
        "A rival gang is encroaching on your territory.",
        "Market prices have shifted unexpectedly.",
        "You received a loan offer from a shady character.",
        "Nothing happened today."
    ]
    return random.choice(events)

MAX_SAFE_TURNS = 3  # Maximum number of turns in one location before risking police encounter

def law_enforcement_encounter():
    """Determine if the player encounters law enforcement."""
    global game_state
    
    # Increment turns in current location
    game_state['turns_in_location'] = game_state.get('turns_in_location', 0) + 1
    
    # Only risk encounter if player has been in the same location for too long
    if game_state['turns_in_location'] > MAX_SAFE_TURNS:
        encounter_chance = random.randint(1, 100)
        if encounter_chance <= 5:  # 5% chance after MAX_SAFE_TURNS
            fine = random.randint(100, 500)
            jail_days = random.randint(1, 2)  # Reduced maximum jail time
            game_state['cash'] = max(0, game_state['cash'] - fine)
            game_state['jail_time'] += jail_days
            message = f"Police encountered you! You were fined ${fine} and sent to jail for {jail_days} day(s)."
            return message
    return None

def process_action(action_data):
    global game_state
    action = action_data.get("action")
    message = ""
    
    if game_state['jail_time'] > 0:
        message = f"You are in jail for {game_state['jail_time']} more days. You cannot perform actions."
        game_state['jail_time'] -= 1
        return message
    
    if action == "buy":
        drug = action_data.get("drug_type")
        amount = action_data.get("amount", 0)
        if not drug or drug not in DRUG_TYPES:
            return "Invalid or missing drug type."
        if not isinstance(amount, int) or amount < 1:
            return "Invalid amount."
        cost = drug_prices[drug] * amount
        if game_state['cash'] >= cost:
            game_state['cash'] -= cost
            game_state['inventory'][drug] += amount
            message = f"Bought {amount} units of {drug} for ${cost}."
        else:
            message = "Insufficient funds to complete the purchase."
    
    elif action == "sell":
        drug = action_data.get("drug_type")
        amount = action_data.get("amount", 0)
        if not drug or drug not in DRUG_TYPES:
            return "Invalid or missing drug type."
        if not isinstance(amount, int) or amount < 1:
            return "Invalid amount."
        if game_state['inventory'].get(drug, 0) >= amount:
            revenue = drug_prices[drug] * amount
            game_state['cash'] += revenue
            game_state['inventory'][drug] -= amount
            message = f"Sold {amount} units of {drug} for ${revenue}."
        else:
            message = f"Not enough {drug} to sell."
    
    elif action == "travel":
        location = action_data.get("location")
        game_state['turns_in_location'] = 0  # Reset turns in location when traveling
        if not location or location not in LOCATIONS:
            return "Invalid or missing location."
        if location == game_state['location']:
            message = "You are already in that location."
        else:
            travel_cost = 100
            if game_state['cash'] >= travel_cost:
                game_state['cash'] -= travel_cost
                game_state['location'] = location
                message = f"Traveled to {location} for ${travel_cost}."
            else:
                message = "Insufficient funds to travel."
    
    elif action == "loan":
        amount = action_data.get("amount", 0)
        if not isinstance(amount, int) or amount < 1:
            return "Invalid loan amount."
        if amount > MAX_LOAN_AMOUNT:
            return f"Loan amount exceeds the maximum of ${MAX_LOAN_AMOUNT}."
        if game_state['debt'] > 0:
            return "You already have an outstanding loan. Repay it first."
        game_state['cash'] += amount
        game_state['debt'] = amount
        game_state['loan_due_date'] = game_state['day'] + LOAN_DURATION
        message = f"Borrowed ${amount}. Repay ${amount + int(amount * LOAN_INTEREST_RATE)} by day {game_state['loan_due_date']}."
    
    elif action == "repay":
        amount = action_data.get("amount", 0)
        if not isinstance(amount, int) or amount < 1:
            return "Invalid repayment amount."
        total_due = game_state['debt'] + int(game_state['debt'] * LOAN_INTEREST_RATE)
        repayment = min(amount, total_due, game_state['cash'])
        game_state['debt'] -= repayment
        game_state['cash'] -= repayment
        if game_state['debt'] <= 0:
            game_state['debt'] = 0
            game_state['loan_due_date'] = None
            message = f"Loan fully repaid. You paid ${repayment}."
        else:
            message = f"Repaid ${repayment}. Remaining debt: ${game_state['debt']}."
    
    elif action == "bank":
        sub_action = action_data.get("sub_action")
        amount = action_data.get("amount", 0)
        if sub_action == "deposit":
            if not isinstance(amount, int) or amount < 1 or amount > game_state['cash']:
                return "Invalid deposit amount."
            game_state['cash'] -= amount
            game_state['bank'] += amount
            message = f"Deposited ${amount} to the bank."
        elif sub_action == "withdraw":
            if not isinstance(amount, int) or amount < 1 or amount > game_state['bank']:
                return "Invalid withdrawal amount."
            game_state['bank'] -= amount
            game_state['cash'] += amount
            message = f"Withdrew ${amount} from the bank."
        else:
            return "Invalid or missing sub_action for bank."
    
    elif action == "quit":
        message = "You have chosen to quit the game."
    
    else:
        message = "Unknown action."
    
    return message

from openai import OpenAI

# Initialize the client
client = OpenAI(
    base_url='http://localhost:11434/v1',
    api_key='ollama'
)

RECALL_TURNS = 5  # Number of recent turns to recall

def update_turn_history(action, result, state_snapshot, prices, event=None):
    turn_info = {
        "day": game_state['day'],
        "action": action,
        "result": result,
        "state": state_snapshot,
        "prices": prices,
        "event": event
    }
    game_state["turn_history"].append(turn_info)
    if len(game_state["turn_history"]) > RECALL_TURNS:
        game_state["turn_history"].pop(0)

def get_user_action(max_retries=3, delay=2, last_event=None):
    """Communicate with the Ollama API to get the AI's next action in the Drug Wars game."""
    attempt = 0
    while attempt < max_retries:
        try:
            # Prepare the current game state as a string
            state_str = (
                f"Day: {game_state['day']}, Cash: ${game_state['cash']}, "
                f"Debt: ${game_state['debt']}, Bank: ${game_state['bank']}, "
                f"Location: {game_state['location']}, "
                f"Inventory: {', '.join([f'{drug}: {qty}' for drug, qty in game_state['inventory'].items() if qty > 0]) or 'Empty'}"
            )
            
            # Include current drug prices
            prices_str = ", ".join([f"{drug}: ${price}" for drug, price in drug_prices.items()])
            
            # Dynamically create cash and debt strings
            cash = f"Cash: ${game_state['cash']}"
            debt = f"Debt: ${game_state['debt']}"
            
            # Include the last event if provided
            event_str = f"Recent event: {last_event}\n" if last_event else ""
            
            # Add a flag to track if we're prompting for reconsideration
            reconsider_prompt = ""
            if attempt > 0:
                reconsider_prompt = (
                    "Your previous action could not be completed due to insufficient funds. "
                    "Please reconsider your action based on your current financial situation. "
                    "You may want to sell some inventory, take a loan, or choose a less expensive action."
                )
                    
            recall_str = "Recalled Recent Events:\n"
            for turn in game_state["turn_history"]:
                recall_str += (f"Day {turn['day']}:\n"
                            f"Action: {turn['action']}\n"
                            f"Result: {turn['result']}\n"
                            f"State: {turn['state']}\n"
                            f"Prices: {turn['prices']}\n"
                            f"Event: {turn['event'] or 'None'}\n\n")

            
            response = client.chat.completions.create(
                model="hermes3",  # Replace with your specific model name if different
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an AI player in a Drug Wars game. Your goal is to maximize profits and avoid legal trouble. "
                            "Make strategic decisions about buying and selling drugs, managing finances, and traveling between locations. "
                            "Analyze the current game state, drug prices, and recent events to determine the best action. "
                            "Respond only with a single JSON object that strictly adheres to the following schema:\n\n"
                            "{\n"
                            '    "action": "buy" | "sell" | "travel" | "loan" | "repay" | "bank" | "quit",\n'
                            '    "drug_type": "cocaine" | "heroin" | "meth" | "weed" | "ecstasy",  // Required for buy/sell actions\n'
                            '    "amount": integer >= 1,  // Required for buy/sell/loan/repay actions\n'
                            '    "location": "Bronx" | "Brooklyn" | "Manhattan" | "Queens" | "Staten Island",  // Required for travel action\n'
                            '    "sub_action": "deposit" | "withdraw"  // Required for bank action\n'
                            "}\n\n"
                            "Include only the necessary fields based on your chosen action. Make intelligent decisions to succeed in the game."
                        )
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{recall_str}\n"
                            f"Current game state: {state_str}\n"
                            f"Current drug prices: {prices_str}\n"
                            f"{event_str}"
                            f"{reconsider_prompt}\n\n"
                            f"""You are an AI player in a Drug Wars game. Your objective is to maximize profits while avoiding legal trouble. You will make strategic decisions about buying and selling drugs, managing finances, and traveling between locations.

The game allows for the following actions:

1. Buy drugs
2. Sell drugs
3. Travel to a new location
4. Take out a loan
5. Repay a loan
6. Perform bank transactions (deposit or withdraw)

Example choices to market conditions:

1. If the price of a drug is low and the demand is high, buy as much as you can afford.
2. If you are in a high-risk area, consider traveling to a safer location.
3. If you have a large amount of cash, consider taking out a loan to expand your operations.
4. If you are in debt, prioritize repaying the loan before making other purchases.
5. If the price of a drug is high, sell some of your inventory to lock in profits.
6. If you are in jail, you cannot perform any actions until you are released.
7. If a recent event suggests market changes, adjust your buying or selling strategy accordingly.

<thinking>
Reflect on your current state:
{state_str}
{prices_str}
{cash}
{debt}
inventory = '{', '.join([f'{drug}: {qty}' for drug, qty in game_state['inventory'].items() if qty > 0]) or 'Empty'}'
location = "Location: {game_state['location']}"
Recent event: {last_event}

Consider your options:
Do you have enough {cash} to make take your next desired action?
Could you take out a loan to cover your expenses? Or sell some of your inventory to cover your expenses?
If your {cash} is 0 or less than $100, you will need to make a frugal choice. Ensure you have enough {cash} to cover your expenses.
How does the recent event affect your decision?
Do you have a diverse inventory of drugs prior to travelling? Anything cheap you can buy and sell for a quick profit?
Can you afford to travel or should you stay and make the most of your current locations low prices?
</thinking>

Your response should be a JSON object that strictly adheres to the following schema:

{{
    "action": "buy" | "sell" | "travel" | "loan" | "repay" | "bank" | "quit",
    "drug_type": "cocaine" | "heroin" | "meth" | "weed" | "ecstasy",  // Required for buy/sell actions
    "amount": integer >= 1,  // Required for buy/sell/loan/repay actions
    "location": "Bronx" | "Brooklyn" | "Manhattan" | "Queens" | "Staten Island",  // Required for travel action
    "sub_action": "deposit" | "withdraw"  // Required for bank action
}}

Provide your response as a single JSON object, following the schema provided earlier. Do not include any explanation or additional text outside of the JSON object.
"""
                        )
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0.5  # Adjust as needed
            )
            
            # Extract JSON response
            action_data = json.loads(response.choices[0].message.content)
            
            # Validate the action data against the schema
            try:
                validate(instance=action_data, schema=action_schema)
                # Check if the action is feasible given the current cash
                if action_data['action'] in ['buy', 'travel'] and 'amount' in action_data:
                    cost = drug_prices[action_data['drug_type']] * action_data['amount'] if action_data['action'] == 'buy' else 100
                    if game_state['cash'] < cost:
                        raise ValueError("Insufficient funds for the proposed action.")
                return action_data
            except (ValidationError, ValueError) as ve:
                console.print(f"[red]Invalid or unfeasible action: {ve}[/red]")
                attempt += 1
                continue  # Skip the rest of the loop and try again

        except Exception as e:
            console.print(f"[red]Error communicating with Ollama API: {e}[/red]")

        attempt += 1
        console.print(f"[yellow]Retrying... ({attempt}/{max_retries})[/yellow]")
        time.sleep(delay)
    
    console.print("[red]Failed to receive a valid action after multiple attempts.[/red]")
    return {}

def law_enforcement_encounter():
    """Determine if the player encounters law enforcement and handle the encounter."""
    global game_state
    
    # Increment turns in current location
    game_state['turns_in_location'] = game_state.get('turns_in_location', 0) + 1
    
    # Only risk encounter if player has been in the same location for too long
    if game_state['turns_in_location'] > MAX_SAFE_TURNS:
        encounter_chance = random.randint(1, 100)
        if encounter_chance <= 5:  # 5% chance after MAX_SAFE_TURNS
            return handle_law_enforcement_options()
    return None

def handle_law_enforcement_options():
    """Present law enforcement encounter options to the LLM and process the decision."""
    fine = random.randint(100, 500)
    bribe_amount = 500
    
    # Prepare the options for the LLM
    options = {
        "pay_fine": f"Pay a fine of ${fine}",
        "lose_inventory": "Lose all of a random drug in your inventory",
        "go_to_jail": "Go to jail for 1-2 days, keeping inventory and cash intact",
        "bribe": f"Bribe the official for ${bribe_amount}"
    }
    
    # Get LLM decision
    decision = get_law_enforcement_decision(options)
    
    # Process the decision
    if decision == "pay_fine":
        game_state['cash'] = max(0, game_state['cash'] - fine)
        return f"You paid a fine of ${fine}."
    elif decision == "lose_inventory":
        if any(game_state['inventory'].values()):
            drug_to_lose = random.choice([drug for drug, qty in game_state['inventory'].items() if qty > 0])
            lost_amount = game_state['inventory'][drug_to_lose]
            game_state['inventory'][drug_to_lose] = 0
            return f"You lost {lost_amount} units of {drug_to_lose}."
        else:
            game_state['cash'] = max(0, game_state['cash'] - fine)
            return f"No inventory to lose. You paid a fine of ${fine} instead."
    elif decision == "go_to_jail":
        jail_days = random.randint(1, 2)
        game_state['jail_time'] = jail_days
        return f"You've been sent to jail for {jail_days} days."
    elif decision == "bribe":
        if game_state['cash'] >= bribe_amount:
            game_state['cash'] -= bribe_amount
            return f"You successfully bribed the official for ${bribe_amount}."
        else:
            game_state['jail_time'] = 1
            return f"Bribe attempt failed due to insufficient funds. You've been sent to jail for 1 day."
    else:
        # Default to jail if something goes wrong
        game_state['jail_time'] = 1
        return "Unexpected response. You've been sent to jail for 1 day."

def get_law_enforcement_decision(options):
    """Communicate with the Ollama API to get the AI's decision for law enforcement encounter."""
    try:
        # Prepare the current game state and options as a string
        state_str = (
            f"Day: {game_state['day']}, Cash: ${game_state['cash']}, "
            f"Debt: ${game_state['debt']}, Bank: ${game_state['bank']}, "
            f"Location: {game_state['location']}, "
            f"Inventory: {', '.join([f'{drug}: {qty}' for drug, qty in game_state['inventory'].items() if qty > 0]) or 'Empty'}"
        )
        
        options_str = "\n".join([f"{key}: {value}" for key, value in options.items()])
        
        response = client.chat.completions.create(
            model="hermes3",  # Replace with your specific model name if different
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an AI player in a Drug Wars game facing a law enforcement encounter. "
                        "Make a strategic decision based on your current game state and the options presented. "
                        "Respond only with one of the following options: pay_fine, lose_inventory, go_to_jail, or bribe."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Current game state: {state_str}\n\n"
                        f"Law enforcement encounter options:\n{options_str}\n\n"
                        "Choose one option based on your current situation and strategy."
                    )
                }
            ],
            temperature=0.5  # Adjust as needed
        )
        
        # Extract decision
        decision = response.choices[0].message.content.strip().lower()
        if decision in options:
            return decision
        else:
            console.print(f"[red]Invalid decision: {decision}. Defaulting to jail.[/red]")
            return "go_to_jail"
    
    except Exception as e:
        console.print(f"[red]Error communicating with Ollama API: {e}. Defaulting to jail.[/red]")
        return "go_to_jail"

def update_loan_status():
    """Check if loan is due and apply penalties if not repaid."""
    if game_state['loan_due_date'] and game_state['day'] >= game_state['loan_due_date']:
        penalty = int(game_state['debt'] * 0.5)  # 50% penalty
        game_state['debt'] += penalty
        game_state['loan_due_date'] += LOAN_DURATION  # Extend the due date
        return f"Loan not repaid on time! A penalty of ${penalty} has been added to your debt. New total debt: ${game_state['debt']}. New due date: Day {game_state['loan_due_date']}."
    return None

def main():
    """Main game loop."""
    console.print(Panel("[bold yellow]Welcome to Drug Wars![/bold yellow]\nManage your resources wisely to succeed.", style="green"))
    last_event = None
    while game_state['day'] <= MAX_DAYS:
        display_status()
        event = generate_random_event()
        console.print(f"[blue]{event}[/blue]")
        last_event = event  # Store the event for the AI's context
        
        # Law enforcement encounter
        police_message = law_enforcement_encounter()
        if police_message:
            console.print(f"[red]{police_message}[/red]")
            last_event = police_message  # Update last_event with police encounter
        
        # Skip LLM prompt if in jail
        if game_state['jail_time'] > 0:
            console.print(f"[red]You are in jail for {game_state['jail_time']} more days.[/red]")
            game_state['jail_time'] -= 1
            game_state['day'] += 1
            continue
        
        # Get user action via Ollama API with retry logic
        console.print("\n[bold cyan]What would you like to do next?[/bold cyan]")
        action_data = get_user_action(last_event=last_event)
        
        if not action_data:
            console.print("[red]Failed to get a valid action. Skipping turn.[/red]")
            game_state['day'] += 1
            continue
        
        message = process_action(action_data)
        if message:
            if "quit" in message.lower():
                console.print(f"[yellow]{message}[/yellow]")
                break
            else:
                console.print(f"[green]{message}[/green]")
        
        # Update turn history with all contexts
        state_snapshot = (f"Cash: ${game_state['cash']}, Debt: ${game_state['debt']}, "
                          f"Location: {game_state['location']}, Inventory: {game_state['inventory']}")
        prices_snapshot = ", ".join([f"{drug}: ${price}" for drug, price in drug_prices.items()])
        update_turn_history(action_data['action'], message, state_snapshot, prices_snapshot, event)
        
        update_prices()
        game_state['day'] += 1
        time.sleep(1)
    
    # Game Over
    console.print(Panel("[bold red]Game Over[/bold red]", style="red"))
    total_assets = (
        game_state['cash'] +
        game_state['bank'] +
        sum([qty * drug_prices[drug] for drug, qty in game_state['inventory'].items()]) -
        game_state['debt']
    )
    console.print(f"Total Assets: ${total_assets}")
    console.print(f"Days Survived: {game_state['day'] - 1}")
    if game_state['debt'] > 0:
        console.print(f"Outstanding Debt: ${game_state['debt']}", style="red")
    console.print("Thank you for playing Drug Wars!")

if __name__ == "__main__":
    main()
