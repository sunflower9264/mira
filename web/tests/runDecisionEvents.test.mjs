import assert from 'node:assert/strict';
import test from 'node:test';

import {
  parseRunResumedEvent,
  reduceRunDecisionState,
} from '../src/lib/runDecisionEvents.ts';

test('historical waiting replay is cleared by run.resumed', () => {
  const waiting = reduceRunDecisionState(
    { waitingInput: null },
    {
      event: 'step.waiting',
      node_id: 'generate_requirements',
      request: {
        context: { title: '信息来源', summary: '请选择信息来源' },
        groups: [],
        request_id: 'request_2',
      },
    },
  );
  const resumed = parseRunResumedEvent('run.resumed', {
    node_id: 'generate_requirements',
  });

  assert.deepEqual(resumed, {
    event: 'run.resumed',
    node_id: 'generate_requirements',
  });
  assert.deepEqual(reduceRunDecisionState(waiting, resumed), {
    status: 'running',
    waitingInput: null,
  });
});
