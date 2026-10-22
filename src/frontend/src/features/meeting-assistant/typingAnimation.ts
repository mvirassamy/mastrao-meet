import type { CSSProperties } from 'react'
import { css } from '@/styled-system/css'

/** Longest time the words of an answer take to appear, whatever its length. */
const TYPING_MS = 1000
const WORD_STEP_MS = 35

export const animationDelay = (ms: number): CSSProperties => ({
  animationDelay: `${ms}ms`,
})

const wordCount = (text: string) => text.split(/\s+/).filter(Boolean).length

/** The pause between two words, shortened for long answers. */
export const wordStep = (text: string) =>
  Math.min(WORD_STEP_MS, TYPING_MS / Math.max(wordCount(text), 1))

/** Starts a block once the given text has written itself out. */
export const afterTyping = (text: string) =>
  animationDelay(Math.round(wordCount(text) * wordStep(text)))

/** Fades a block in, for an answer that just arrived. */
export const revealIn = css({
  animation: 'reveal 0.28s cubic-bezier(0.2, 0.7, 0.2, 1) both',
  _motionReduce: { animation: 'none' },
})
