import sys, time, os
from modules import *
from decimal import Decimal
from dotenv import dotenv_values
from itertools import combinations
from brownie import accounts, network
from SETTINGS import stable_coin_dict, univ2_forks_dict

SLIPPAGE = Decimal("0.001")  # tolerated slippage in swap price (0.1%)

# Simulate swaps and approvals
DRY_RUN = False
# Quit after the first successful trade
ONE_SHOT = True
# How often to run the main loop (in seconds)
LOOP_TIME = 0.25

CONFIG_FILE = "univ2_stable_arb_bot.env"
BROWNIE_NETWORK = dotenv_values(CONFIG_FILE)["BROWNIE_NETWORK"]
BROWNIE_ACCOUNT = dotenv_values(CONFIG_FILE)["BROWNIE_ACCOUNT"]
os.environ["SNOWTRACE_TOKEN"] = dotenv_values(CONFIG_FILE)["SNOWTRACE_API_KEY"]

POOL_NOT_FOUND_ADDR = "0x0000000000000000000000000000000000000000"
# swap only if profit (not considering gas fees) is higher than 0.7% of total stable amount in walllet.
# value adjusted for total stable amount ~30 USD and gas fee ~0.2 USD
TOTAL_STABLE_AMOUNT_SWAP_THREESHOLD = Decimal("0.007")

# TODO: Calculate Estimated Swap Value including gas fees impact on swap target value.


def main():

    try:
        network.connect(BROWNIE_NETWORK)
    except:
        sys.exit("Could not connect! Verify your Brownie network settings using 'brownie networks list'")

    try:
        degenbot = accounts.load(BROWNIE_ACCOUNT)
        user = degenbot
    except:
        sys.exit("Could not load account! Verify your Brownie account settings using 'brownie accounts list'")

    tokens = [Erc20Token(address=value, user=user, abi=ERC20) for value in stable_coin_dict[BROWNIE_NETWORK].values()]
    token_pairs = list(combinations(tokens, 2))  # list of tuples

    factories = [
        LPFactory(
            address=value["FACTORY"],
            name=key,
            router=Router(address=value["ROUTER"], name=key, user=user, abi=UNISWAPV2_ROUTER),
            abi=UNISWAPV2_FACTORY,
        )
        for key, value in univ2_forks_dict[BROWNIE_NETWORK].items()
    ]

    lps = []
    for factory in factories:
        for pair in token_pairs:
            token1_addr, token2_addr = pair[0].address, pair[1].address
            token1_name, token2_name = pair[0].name, pair[1].name
            lp_addr = factory.contract.getPair(token1_addr, token2_addr)
            if lp_addr != POOL_NOT_FOUND_ADDR:
                lps.append(
                    LiquidityPool(
                        address=lp_addr,
                        name=f"{factory.name}: {token1_name}-{token2_name}",
                        router=factory.router,
                        abi=UNISWAPV2_LP,
                        tokens=list(pair),
                    )
                )
            else:
                print(f"WARNING: PAIR {factory.name}: {token1_name}-{token2_name} NOT FOUND")

    # Confirm approvals for all tokens on every router
    print()
    print("Approvals:")
    for factory in factories:
        router = factory.router
        for token in tokens:
            if not token.get_approval(external_address=router.address) and not DRY_RUN and token.normalized_balance > 1:
                token.set_approval(external_address=router.address, value=-1)
            else:
                print(f"{token} on {router} OK")
    print()

    balance_refresh = True

    #
    # Start of main loop
    #

    while True:

        try:
            if network.is_connected():
                pass
            else:
                print("Network connection lost! Reconnecting...")
                if network.connect(BROWNIE_NETWORK):
                    pass
                else:
                    time.sleep(5)
                    continue
        except:
            # restart loop
            continue

        loop_start = time.time()

        if balance_refresh:
            total_stable_amount = 0
            print()
            print("Account Balance:")
            for token in tokens:
                token.update_balance()
                print(f"• {token.normalized_balance} {token.symbol} ({token.name})")
                total_stable_amount += token.normalized_balance  # tokens can have different decimals
                balance_refresh = False
            print(f"TOTAL STABLE AMOUNT: {total_stable_amount}")

            # Set token swap ratios
            # As we are maximizing the total amount of stables, the min target swap ratio for a given token is the same for all pairs

            for token in tokens:
                token_target_swap_ratio = (
                    (
                        (1 + float(TOTAL_STABLE_AMOUNT_SWAP_THREESHOLD)) * total_stable_amount
                        - (total_stable_amount - token.normalized_balance)
                    )
                    / token.normalized_balance
                    if token.normalized_balance > 1.0  # Dust balances
                    else 0.0
                )
                token_target_swap_ratio = 1 / token_target_swap_ratio if token_target_swap_ratio > 0.0 else 0.0
                token.set_target_swap_ratio(token_target_swap_ratio)

        for lp in lps:
            # print("Swap Targets:")
            lp.set_lp_swap_target_ratio(lp.token0, lp.token1, silent=False)
            lp.set_lp_swap_target_ratio(lp.token1, lp.token0, silent=False)
            lp.calculate_tokens_in_from_ratio_out()

            lp.update_reserves(print_reserves=False)

            if (
                lp.token0_max_swap and lp.token0.balance and lp.token0_max_swap >= lp.token0.balance
            ):  # Formula used to calculate target swap ratio assumes that all balance can be traded
                token_in = lp.token0
                token_out = lp.token1
                # finds maximum token1 input at desired ratio
                # token_in_qty = min(lp.token0.balance, lp.token0_max_swap)
                token_in_qty = lp.token0.balance
                # calculate output from maximum input above
                token_out_qty = lp.calculate_tokens_out_from_tokens_in(
                    token_in=lp.token0,
                    token_in_quantity=token_in_qty,
                )
                print(
                    f"*** EXECUTING SWAP ON {str(lp.router).upper()} OF {token_in_qty / (10 ** token_in.decimals)} {token_in} FOR {token_out_qty / (10 ** token_out.decimals)} {token_out} ***"
                )
                if not DRY_RUN:
                    lp.router.token_swap(
                        token_in_quantity=token_in_qty,
                        token_in_address=token_in.address,
                        token_out_quantity=token_out_qty,
                        token_out_address=token_out.address,
                        slippage=SLIPPAGE,
                    )
                    balance_refresh = True
                    if ONE_SHOT:
                        sys.exit("single shot complete!")
                    break

            if (
                lp.token1_max_swap and lp.token1.balance and lp.token1_max_swap >= lp.token1.balance
            ):  # Formula used to calculate target swap ratio assumes that all balance can be traded
                token_in = lp.token1
                token_out = lp.token0
                # finds maximum token1 input at desired ratio
                # token_in_qty = min(lp.token1.balance, lp.token1_max_swap)
                token_in_qty = lp.token1.balance
                # calculate output from maximum input above
                token_out_qty = lp.calculate_tokens_out_from_tokens_in(
                    token_in=lp.token1,
                    token_in_quantity=token_in_qty,
                )
                print(
                    f"*** EXECUTING SWAP ON {str(lp.router).upper()} OF {token_in_qty / (10 ** token_in.decimals)} {token_in} FOR {token_out_qty / (10 ** token_out.decimals)} {token_out} ***"
                )
                if not DRY_RUN:
                    lp.router.token_swap(
                        token_in_quantity=token_in_qty,
                        token_in_address=token_in.address,
                        token_out_quantity=token_out_qty,
                        token_out_address=token_out.address,
                        slippage=SLIPPAGE,
                    )
                    balance_refresh = True
                    if ONE_SHOT:
                        sys.exit("single shot complete!")
                    break

        loop_end = time.time()

        # Control the loop timing more precisely by measuring start and end time and sleeping as needed
        if (loop_end - loop_start) >= LOOP_TIME:
            continue
        else:
            time.sleep(LOOP_TIME - (loop_end - loop_start))
            continue

    #
    # End of main loop
    #


# Only executes main loop if this file is called directly
if __name__ == "__main__":
    main()
