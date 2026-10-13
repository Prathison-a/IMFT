"""Generic random-search parameter optimizer matching StrategyOptimizer.cs."""
import random, math
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from strategy.models import BacktestResult, StrategyParameterType
from strategy.strategies import BaseStrategy

@dataclass
class Trial:
    params: Dict[str, Any]
    result: BacktestResult

    def to_dict(self) -> Dict[str, Any]:
        return {
            "params": self.params,
            "result": self.result.to_dict(),
        }

class StrategyOptimizer:
    @staticmethod
    def sample_params(strategy: BaseStrategy, rng: random.Random) -> Dict[str, Any]:
        p = {}
        for definition in strategy.parameter_definitions:
            k = definition.key
            t = definition.type
            d = definition.default_value
            if t == "bool" or t == "Bool":
                p[k] = rng.choice([True, False])
            elif t == "int" or t == "Int":
                val = int(d)
                lo = max(1, int(round(val * 0.5)))
                hi = max(lo, int(round(val * 2.0)))
                p[k] = rng.randint(lo, hi)
            elif t == "double" or t == "Double":
                val = float(d)
                if val == 0:
                    p[k] = rng.random()
                else:
                    lo = min(val * 0.5, val * 2.0)
                    hi = max(val * 0.5, val * 2.0)
                    p[k] = round(lo + rng.random() * (hi - lo), 3)
            else:
                p[k] = d
        return p

    @classmethod
    def search(
        cls,
        strategy: BaseStrategy,
        exec_bars,
        anchor_bars,
        initial_equity: float = 100000.0,
        trials: int = 50,
        seed: Optional[int] = None
    ) -> List[Trial]:
        rng = random.Random(seed)
        results: List[Trial] = []
        seen = set()
        attempts = 0
        max_attempts = trials * 20

        while len(results) < trials and attempts < max_attempts:
            attempts += 1
            p = cls.sample_params(strategy, rng)
            key = "|".join(f"{k}={p[k]}" for k in sorted(p.keys()))
            if key in seen:
                continue
            seen.add(key)

            try:
                res = strategy.run(exec_bars, anchor_bars, p, initial_equity)
            except Exception:
                continue

            results.append(Trial(params=p, result=res))

        return results

    @staticmethod
    def pick_best(trials: List[Trial], target_sharpe: float = 1.0, min_trades: int = 10) -> Optional[Trial]:
        qualifying = [
            t for t in trials
            if not math.isnan(t.result.sharpe) and t.result.sharpe >= target_sharpe and t.result.num_trades >= min_trades
        ]
        if qualifying:
            # Score = Sharpe * sqrt(trades)
            return max(qualifying, key=lambda t: t.result.sharpe * math.sqrt(t.result.num_trades))

        enough_trades = [
            t for t in trials
            if not math.isnan(t.result.sharpe) and t.result.num_trades >= min_trades
        ]
        pool = enough_trades if enough_trades else [t for t in trials if not math.isnan(t.result.sharpe)]
        if pool:
            return max(pool, key=lambda t: t.result.sharpe)
        return trials[0] if trials else None


def run_monte_carlo_optimization(strategy, exec_bars, anchor_bars=None, initial_equity: float = 100000.0, max_evals: int = 50):
    from strategy.strategies import StrategyRegistry
    strat_obj = StrategyRegistry.get_by_name(strategy) if isinstance(strategy, str) else strategy
    if anchor_bars is None:
        anchor_bars = exec_bars
    return StrategyOptimizer.search(strat_obj, exec_bars, anchor_bars, initial_equity, trials=max_evals)
