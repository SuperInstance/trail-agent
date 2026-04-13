# Trail Agent Workshop

## Structure

```
workshop/
  recipes/
    hot/   — Immediate-use trail recipes (1-3 steps)
    med/   — Multi-step trail recipes (5-15 steps)
    cold/  — Advanced/composable trail recipes (10+ steps)
  bootcamp/ — Learning exercises
  dojo/     — Mastery challenges
```

## Recipes

### Hot Recipes (Quick Start)
- `basic_file_ops.json` — File read, search, write
- `git_workflow.json` — Edit, test, commit, push

### Med Recipes (Intermediate)
- `trust_communication.json` — Trust updates, bottle messages, labels, caps

### Cold Recipes (Advanced)
- `composable_trail.json` — Branches, labels, NOPs, composition boundaries

## Bootcamp Exercises

1. **Hello Trail**: Create a trail with TRAIL_BEGIN, one FILE_READ, TRAIL_END
2. **Round Trip**: Encode a trail, decode it, verify the steps match
3. **Compiler**: Write worklog entries and compile them to bytecode
4. **Execution**: Run a trail with MockWorld and verify call order
5. **Verification**: Create valid and invalid trails, run all 6 checks

## Dojo Challenges

1. **Concatenation**: Build two trails and concatenate them seamlessly
2. **Meta-Trail**: Execute a trail and inspect the execution meta-trail
3. **Custom World**: Implement a WorldInterface that logs to a file
4. **Trust Chain**: Build a 3-agent trust-building trail with bottles
5. **Fingerprint**: Prove that identical trails produce identical fingerprints
