from engineutils import prob_by_roll_result, compute_successes_ratio, DiceExpr, float_eq


class State:
    def __init__(self,
                 n_unsaved_wounds_left,  # key field, 0 when resolved
                 current_wound_n_damages_left,  # key field, 0 when resolved
                 n_figs_slained_so_far,  # value field
                 remaining_target_wounds,  # key field
                 ):
        self.n_unsaved_wounds_left = n_unsaved_wounds_left
        self.current_wound_n_damages_left = current_wound_n_damages_left
        self.n_figs_slained_so_far = n_figs_slained_so_far
        self.remaining_target_wounds = remaining_target_wounds

    def copy(self):
        return State(self.n_unsaved_wounds_left,
                     self.current_wound_n_damages_left,
                     self.n_figs_slained_so_far,
                     self.remaining_target_wounds)


class Cache:
    def __init__(self):
        self.dict = {}
        self.hits = 0
        self.tries = 0

    def __str__(self):
        return f"tries={self.tries}, hits={self.hits}, misses={self.tries - self.hits}"

    def add(self, state, downstream):
        """
        Store a copy of downstream
        """
        key = Cache._keyify(state)
        res = self.dict.get(key, None)
        if res is None:
            self.dict[key] = downstream[:]
        elif len(res) < len(downstream):
            # print(downstream, "replaced", res)
            self.dict[key] = downstream[:]

    def get(self, state):
        res = self.dict.get(Cache._keyify(state), None)
        self.tries += 1
        if res is not None:
            self.hits += 1
        return res

    def reset(self):
        del self.dict
        self.dict = {}
        self.hits = 0
        self.tries = 0

    @staticmethod
    def _keyify(state):
        return f"{state.current_wound_n_damages_left},{state.remaining_target_wounds}"


class Node:
    weapon_d = None
    target_wounds = None
    n_unsaved_wounds_init = None
    n_figs_slained_weighted_ratios = None
    fnp_fail_ratio = None
    start_target_wounds = None
    cache = Cache()

    def __init__(self, state, parents_states, children_states):
        self.state = state
        self.parents_states = parents_states
        self.children_states = children_states


def element_wise_sum(*ls):
    assert(all(map(lambda l: len(l) == len(ls[0]), ls)))
    return list(map(lambda elems: sum(elems), zip(*ls)))


def scalar_mult_list_elems(k, l):
    assert(type(l) is list)
    try:
        k = float(k)
    except:
        raise AttributeError("k must be a number")
    return list(map(lambda e: k * e, l))


def scalar_add_list_elems(b, l):
    assert(type(l) is list)
    try:
        b = float(b)
    except:
        raise AttributeError("k must be a number")
    return list(map(lambda e: b + e, l))


# TODO: make all the sub triangle cached, not only the one going from node X to leafs: more cache hits
def compute_slained_figs_frac(state_):
    assert (isinstance(state_, State))
    assert (state_.remaining_target_wounds >= 0)
    assert (state_.n_unsaved_wounds_left >= 0)
    assert (state_.current_wound_n_damages_left >= 0)
    state = state_.copy()

    # resolve a model kill
    if state.remaining_target_wounds == 0:
        state.remaining_target_wounds = Node.target_wounds
        # additionnal damages are not propagated to other models
        state.current_wound_n_damages_left = 0
        downstream = compute_slained_figs_frac(state)
        downstream = scalar_add_list_elems(1, downstream)
        #downstream[0] += 1
        return downstream   # upstream propagation of figs slained count

    last_model_injured_frac = 1 - state.remaining_target_wounds / Node.target_wounds

    if state.current_wound_n_damages_left == 0 and state.n_unsaved_wounds_left == 0:
        # leaf: no more damages to fnp no more wounds to consume or p(leaf) < threshold
        # portion of the last model injured
        Node.cache.add(state, [last_model_injured_frac])
        return [last_model_injured_frac]
    else:
        # test cache
        cached_downstream = Node.cache.get(state)
        if cached_downstream is not None and state.n_unsaved_wounds_left < len(cached_downstream):
            # use cached res if deep enough
            relevant_cached_downstream = cached_downstream[len(cached_downstream) - state.n_unsaved_wounds_left - 1:]
            return relevant_cached_downstream
        else:
            if state.current_wound_n_damages_left == 0:
                if cached_downstream is None or state.n_unsaved_wounds_left >= len(cached_downstream):
                    # consume a wound
                    # random doms handling
                    res = [
                        scalar_mult_list_elems(
                            prob_d,
                            compute_slained_figs_frac(State(n_unsaved_wounds_left=state.n_unsaved_wounds_left - 1,
                                                               current_wound_n_damages_left=d,
                                                               n_figs_slained_so_far=state.n_figs_slained_so_far,
                                                               remaining_target_wounds=state.remaining_target_wounds))
                        )
                        for d, prob_d in prob_by_roll_result(Node.weapon_d).items()]
                    downstream = element_wise_sum(*res)
                    Node.cache.add(state, downstream)
                    downstream.append(last_model_injured_frac)
                    return downstream
            else:
                # FNP fail
                f = compute_slained_figs_frac(State(state.n_unsaved_wounds_left,
                                                    state.current_wound_n_damages_left - 1,
                                                    state.n_figs_slained_so_far,
                                                    state.remaining_target_wounds - 1))

                # FNP success
                if Node.fnp_fail_ratio != 1:
                    s = compute_slained_figs_frac(State(state.n_unsaved_wounds_left,
                                                        state.current_wound_n_damages_left - 1,
                                                        state.n_figs_slained_so_far,
                                                        state.remaining_target_wounds))
                    downstream = element_wise_sum(
                        scalar_mult_list_elems(1 - Node.fnp_fail_ratio, s),
                        scalar_mult_list_elems(Node.fnp_fail_ratio, f)
                    )
                else:
                    downstream = scalar_mult_list_elems(Node.fnp_fail_ratio, f)
                Node.cache.add(state, downstream)
                return downstream




def compute_slained_figs_ratios_per_unsaved_wound(weapon_d, target_fnp, target_wounds, n_unsaved_wounds_init=32):
    """
    n_unsaved_wounds_init=100: 57 sec
                           64: 38 sec, res prec +-0.01
                           50: 22 sec, res prec +-0.02
                           40: 23 sec, res prec +-0.015
                           32: 18 sec, res prec +-0.02
                           16: 10 sec, res prec +-0.05
                           8: 5.8 sec, res prec +-0.2
    """
    Node.weapon_d = weapon_d
    Node.target_wounds = target_wounds
    Node.n_unsaved_wounds_init = n_unsaved_wounds_init
    Node.n_figs_slained_weighted_ratios = []
    Node.fnp_fail_ratio = 1 if target_fnp is None else 1 - compute_successes_ratio(target_fnp)
    Node.start_target_wounds = target_wounds
    Node.cache.reset()

    return compute_slained_figs_frac(State(
        n_unsaved_wounds_left=n_unsaved_wounds_init,
        current_wound_n_damages_left=0,
        n_figs_slained_so_far=0,
        remaining_target_wounds=target_wounds))[0] / Node.n_unsaved_wounds_init

import enginev2, enginev3legacy
print(enginev2.compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(1, 3), 6, 6, n_unsaved_wounds_init=4))
print(enginev3legacy.compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(1, 3), 6, 6, n_unsaved_wounds_init=4))
print(enginev3legacy.Node.cache)
print(enginev3legacy.Node.cache.dict)
print(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(1, 3), 6, 6, n_unsaved_wounds_init=4))
print(Node.cache)
print(Node.cache.dict)
exit(0)
# FNP
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(1), 6, 1), 5 / 6, 0))
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(1), 5, 1), 4 / 6, 0))
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(1), 4, 1), 0.5, 0))
# on W=2
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(1), None, 2), 0.5, 0))
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(2), None, 2), 1, 0))
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(2, 3), None, 2), 1, 0))
# random doms
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(1, 6), None, 35), 0.1, 0))
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(
DiceExpr(1, 6), 4, 175), 0.01, 0)
)

assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(
DiceExpr(1, 6), 5, 70, n_unsaved_wounds_init=70), 2 / 3 * 3.5 / 70, 0)
)
# lost damages
assert (float_eq(compute_slained_figs_ratios_per_unsaved_wound(DiceExpr(5), target_fnp=None, target_wounds=6,
                                                           n_unsaved_wounds_init=33), 0.5, 0))
