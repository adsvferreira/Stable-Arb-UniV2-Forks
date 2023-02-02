from brownie import Contract
from modules.router import Router
from brownie.convert import to_address


class LPFactory:
    def __init__(self, address: str, name: str, router: Router, abi: list = None) -> None:

        self.name = name
        # transforms to checksummed address
        try:
            self.address = to_address(address)
        except ValueError:
            print("Could not checksum address, storing non-checksummed version")
            self.address = address

        try:
            self.contract = Contract(self.address)
        except:
            if abi:
                self.contract = Contract.from_abi(name="", abi=abi, address=self.address)
                self.abi = abi
            else:
                self.contract = Contract.from_explorer(address=self.address)
        else:
            self.abi = self.contract.abi

        self.router = router
