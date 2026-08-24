import type { DecisionGroup, DecisionRequestContext, RunEvent } from '../types';

export interface RunResumedEvent {
  event: 'run.resumed';
  node_id: string;
}

export interface RunDecisionState {
  status: 'running' | 'waiting_for_user';
  waitingInput: {
    node_id: string;
    context: DecisionRequestContext;
    groups: DecisionGroup[];
    request_id: string;
  } | null;
}

type RunDecisionEvent =
  | Extract<RunEvent, { event: 'step.waiting' }>
  | Extract<RunEvent, { event: 'run.waiting_for_user' }>
  | RunResumedEvent;

export function parseRunResumedEvent(
  event: string,
  payload: Record<string, unknown>,
): RunResumedEvent | null {
  if (event !== 'run.resumed' || typeof payload.node_id !== 'string') return null;
  return { event: 'run.resumed', node_id: payload.node_id };
}

export function reduceRunDecisionState(
  state: Pick<RunDecisionState, 'waitingInput'>,
  event: RunDecisionEvent,
): RunDecisionState {
  if (event.event === 'step.waiting') {
    return {
      status: 'waiting_for_user',
      waitingInput: {
        node_id: event.node_id,
        context: event.request.context,
        groups: event.request.groups ?? [],
        request_id: event.request.request_id,
      },
    };
  }
  if (event.event === 'run.waiting_for_user') {
    return { status: 'waiting_for_user', waitingInput: state.waitingInput };
  }
  return { status: 'running', waitingInput: null };
}
