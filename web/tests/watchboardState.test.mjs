import assert from 'node:assert/strict'

import {
  buildIntradayReviewQuestion,
  computeCardState,
  computeStateMachineState,
  formatWatchPrice,
  intradayReviewLabel,
} from '../src/utils/watchboardState.js'

const legacyOnlyItem = {
  price: 100,
  reasoning_summary: {
    action: '观望',
    one_liner: '旧摘要不应优先',
    key_level_down: 95,
    key_level_up: 110,
  },
  monitor_conditions: {
    triggers: [
      {
        type: 'price_below',
        level: 95,
        message_on_trigger: '接近支撑，观察承接',
        action_on_trigger: '关注',
      },
      {
        type: 'price_above',
        level: 110,
        message_on_trigger: '突破确认',
        action_on_trigger: '关注',
      },
    ],
  },
}

assert.equal(computeStateMachineState(legacyOnlyItem, 111).available, false)
assert.equal(computeCardState(legacyOnlyItem, 111), 'normal')
assert.equal(buildIntradayReviewQuestion(legacyOnlyItem, 111), '')
assert.equal(intradayReviewLabel(legacyOnlyItem, 111), '')
assert.equal(formatWatchPrice(40), '40')
assert.equal(formatWatchPrice(253.49), '253.49')

const machineItem = {
  symbol: 'sh.603893',
  price: 193.46,
  reasoning_summary: {
    one_liner: '旧摘要不应优先',
    action: '持仓观察',
    watch_state_machine: {
      version: 'watch_state_machine.v1',
      current_state: {
        name: '30分钟中枢震荡',
        level: '5分钟',
        range: [189.48, 200.96],
        display: '中枢内，等方向选择',
      },
      transitions: [
        {
          id: 'up_break',
          trigger: { type: 'price_above', level: 195.96 },
          next_state: '多头延续确认',
          observe: '站上压力后看回踩',
          success: '回踩不破看买点确认',
          failure: '跌回中枢看离开失败',
          next_watch: '继续看上方压力',
        },
        {
          id: 'down_break',
          trigger: { type: 'price_below', level: 189.48 },
          next_state: '支撑测试',
          observe: '跌破下沿看能否拉回',
          success: '拉回中枢看反抽',
          failure: '拉不回则结构转弱',
          next_watch: '观察反抽卖点',
        },
      ],
    },
  },
}

const baseState = computeStateMachineState(machineItem, 193.46)
assert.equal(baseState.available, true)
assert.equal(baseState.displayLine, '中枢内，等方向选择')
assert.equal(baseState.state, 'idle')
assert.equal(baseState.nextWatchLine, '')
assert.equal(intradayReviewLabel(machineItem, 193.46), '1m区间复核')
assert.match(buildIntradayReviewQuestion(machineItem, 193.46), /尚未触发状态机分支/)

const upState = computeStateMachineState(machineItem, 196)
assert.equal(upState.state, 'confirmed')
assert.equal(upState.isFreshTrigger, false)
assert.equal(upState.displayLine, '站上压力后看回踩')
assert.equal(upState.nextWatchLine, '')
const reviewQuestion = buildIntradayReviewQuestion(machineItem, 196)
assert.match(reviewQuestion, /盘中1分钟复核/)
assert.match(reviewQuestion, /当前价196.00已经在195.96上方/)
assert.match(reviewQuestion, /如果盘中1分钟K线/)
assert.equal(intradayReviewLabel(machineItem, 196), '1m状态复核')

const freshUpState = computeStateMachineState(machineItem, 196, 195.5)
assert.equal(freshUpState.state, 'confirmed')
assert.equal(freshUpState.isFreshTrigger, true)
assert.equal(freshUpState.nextWatchLine, '回踩不破看买点确认')
assert.match(buildIntradayReviewQuestion(machineItem, 196, 195.5), /当前价196.00刚刚站上195.96/)
assert.equal(intradayReviewLabel(machineItem, 196, 195.5), '1m复核触发')

const quietAfterUp = computeStateMachineState(machineItem, 193.7)
assert.equal(quietAfterUp.state, 'idle')
assert.equal(quietAfterUp.displayLine, '中枢内，等方向选择')
assert.equal(quietAfterUp.nextWatchLine, '')

const downState = computeStateMachineState(machineItem, 188.8)
assert.equal(downState.state, 'alert')
assert.equal(downState.isFreshTrigger, false)
assert.equal(downState.displayLine, '跌破下沿看能否拉回')
assert.equal(downState.nextWatchLine, '')

const freshDownState = computeStateMachineState(machineItem, 188.8, 190)
assert.equal(freshDownState.state, 'alert')
assert.equal(freshDownState.isFreshTrigger, true)
assert.equal(freshDownState.nextWatchLine, '拉回中枢看反抽')
assert.match(buildIntradayReviewQuestion(machineItem, 188.8, 190), /当前价188.80刚刚跌破189.48/)

console.log('watchboardState tests passed')
