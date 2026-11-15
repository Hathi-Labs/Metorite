# E9b

Tier: HIGH

Gate: AGENT-SAFE. After E9a

## What

**The clock stops during a pause** (task 3122). Design: a pause moves the due time forward by the time that the record spent in the pause, so a record that waits on a customer keeps its SLA time. **Not in this slice:** the bell (E9c) and working hours (E9d)

## Done when

A record that spent 2 h in a pause breaches 2 h after its due value. A record that never paused breaches at its due value
