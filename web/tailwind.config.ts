import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // PRD §7 design tokens
        canvas: '#F4F5F7',
        ink: '#0B0B0F',
        // Node palette (PRD §7)
        nodeYellow: '#FFEEC2',
        nodeBlue: '#C9DCFF',
        nodePurple: '#D8D2FF',
        nodeGreen: '#C8E6C9',
        // Condition (judgement) 节点配色，区别于 generate 的蓝
        nodeOrange: '#FFD9B0',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        serif: ['Georgia', 'ui-serif', 'serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        card: '16px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.04)',
        pill: '0 2px 8px rgba(0,0,0,0.06)',
      },
    },
  },
  plugins: [],
};
export default config;
