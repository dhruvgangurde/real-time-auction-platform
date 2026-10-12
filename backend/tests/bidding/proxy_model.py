"""Pure-Python reference model of arbitration and proxy resolution (FR-36 to FR-39, FR-45 to FR-51).

Test code only. It restates the rules independently of the Lua scripts so the property-based
test can cross-check every step. The auction is assumed live; time rules are tested elsewhere.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Outcome:
    decision: str  # accepted | armed | rejected
    code: str | None = None
    autos: tuple[tuple[str, int], ...] = ()  # (bidder, amount) automatic bids, in order


@dataclass
class AuctionModel:
    start_price: int
    inc: int
    self_outbid: bool = False
    highest: int = 0
    leader: str | None = None
    seq: int = 0
    arm_seq: int = 0
    proxies: dict[str, tuple[int, int]] = field(default_factory=dict)  # bidder -> (max, arm)
    ledger: list[tuple[str, int, str]] = field(default_factory=list)  # (bidder, amount, type)

    def min_next(self) -> int:
        return self.highest + self.inc if self.seq else self.start_price

    def _accept(self, bidder: str, amount: int, kind: str) -> tuple[str, int]:
        self.seq += 1
        self.highest = amount
        self.leader = bidder
        self.ledger.append((bidder, amount, kind))
        return bidder, amount

    def place_bid(self, bidder: str, amount: int) -> Outcome:
        if amount < self.min_next():
            return Outcome("rejected", "BID_TOO_LOW")
        if amount > 100 * self.start_price:
            return Outcome("rejected", "BID_ABOVE_LIMIT")
        if self.self_outbid and bidder == self.leader:
            return Outcome("rejected", "SELF_OUTBID_NOT_ALLOWED")
        self._accept(bidder, amount, "manual")
        return Outcome("accepted", autos=self._resolve())

    def set_proxy(self, bidder: str, max_amount: int) -> Outcome:
        if max_amount < self.min_next():
            return Outcome("rejected", "BELOW_CURRENT_PRICE")
        if max_amount > 100 * self.start_price:
            return Outcome("rejected", "BID_ABOVE_LIMIT")
        if bidder in self.proxies and max_amount <= self.proxies[bidder][0]:
            return Outcome("rejected", "PROXY_CANNOT_DECREASE")
        self.arm_seq += 1  # a raise takes a new arm_seq: priority resets (FR-49 tie rule)
        self.proxies[bidder] = (max_amount, self.arm_seq)
        return Outcome("armed", autos=self._resolve())

    def _resolve(self) -> tuple[tuple[str, int], ...]:
        """Closed form (FR-47 to FR-49): at most two automatic bids per trigger."""
        autos: list[tuple[str, int]] = []
        order = sorted(self.proxies.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
        if order:
            top, (top_max, _) = order[0]
            second = order[1] if len(order) > 1 else None
            if top == self.leader or top_max > self.highest:
                h0, l0 = self.highest, self.leader
                competing = max(h0, second[1][0]) if second else h0
                floor = competing + self.inc if competing > 0 else self.start_price
                target = min(top_max, floor)
                challenged = second is not None and second[1][0] > h0
                if top != l0 or challenged:
                    if challenged and second is not None and second[1][0] < target:
                        autos.append(self._accept(second[0], second[1][0], "automatic"))
                    autos.append(self._accept(top, target, "automatic"))
        # exhausted: not the leader and no longer above the highest bid (UC-02 5a)
        for bidder, (max_amount, _) in list(self.proxies.items()):
            if bidder != self.leader and max_amount <= self.highest:
                del self.proxies[bidder]
        return tuple(autos)
