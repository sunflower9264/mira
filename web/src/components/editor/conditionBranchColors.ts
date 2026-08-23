import { CONDITION_DEFAULT_BRANCH_KEY } from '../../types';

interface ConditionBranchVisual {
  color: string;
  selectedColor: string;
  backgroundColor: string;
}

const TRUE_BRANCH: ConditionBranchVisual = {
  color: '#6F927E',
  selectedColor: '#4F745F',
  backgroundColor: 'rgba(111, 146, 126, 0.09)',
};

const FALSE_BRANCH: ConditionBranchVisual = {
  color: '#A47E78',
  selectedColor: '#805D58',
  backgroundColor: 'rgba(164, 126, 120, 0.09)',
};

const DEFAULT_BRANCH: ConditionBranchVisual = {
  color: '#8B929A',
  selectedColor: '#626A73',
  backgroundColor: 'rgba(139, 146, 154, 0.08)',
};

const CUSTOM_HUE_RANGES: Array<[number, number]> = [
  [28, 58],
  [178, 238],
  [248, 328],
];

export function conditionBranchVisual(nodeId: string, branchKey: string): ConditionBranchVisual {
  if (branchKey === 'true') return TRUE_BRANCH;
  if (branchKey === 'false') return FALSE_BRANCH;
  if (branchKey === CONDITION_DEFAULT_BRANCH_KEY) return DEFAULT_BRANCH;

  const hash = stableHash(`${nodeId}\u0000${branchKey}`);
  const hue = hueFromHash(hash);
  const saturation = 24 + ((hash >>> 8) % 8);
  const lightness = 47 + ((hash >>> 12) % 5);

  return {
    color: `hsl(${hue} ${saturation}% ${lightness}%)`,
    selectedColor: `hsl(${hue} ${Math.min(38, saturation + 4)}% ${Math.max(34, lightness - 10)}%)`,
    backgroundColor: `hsl(${hue} ${saturation}% ${lightness}% / 0.09)`,
  };
}

function hueFromHash(hash: number): number {
  const total = CUSTOM_HUE_RANGES.reduce((sum, [start, end]) => sum + end - start + 1, 0);
  let offset = hash % total;
  for (const [start, end] of CUSTOM_HUE_RANGES) {
    const size = end - start + 1;
    if (offset < size) return start + offset;
    offset -= size;
  }
  return CUSTOM_HUE_RANGES[0][0];
}

function stableHash(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}
