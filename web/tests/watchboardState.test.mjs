import assert from 'node:assert/strict'

import { computeTacticalState, formatWatchPrice } from '../src/utils/watchboardState.js'

const baseItem = {
  price: 100,
  reasoning_summary: {
    action: '观望',
    one_liner: '等待结构确认',
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
        type: 'price_below',
        level: 90,
        message_on_trigger: '跌破失败线，路径失效',
        action_on_trigger: '止损',
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

assert.equal(computeTacticalState(baseItem, 100).state, 'idle')
assert.equal(computeTacticalState(baseItem, 96).state, 'near')
assert.equal(computeTacticalState(baseItem, 89).state, 'invalid')
assert.equal(computeTacticalState(baseItem, 111).state, 'confirmed')
assert.equal(computeTacticalState(baseItem, 100).displayLine, '等待结构确认')
assert.equal(computeTacticalState(baseItem, 89).displayLine, '跌破失败线，路径失效')
assert.equal(computeTacticalState(baseItem, 111).displayLine, '突破确认')
assert.equal(
  computeTacticalState({
    monitor_conditions: {
      triggers: [
        {
          type: 'price_below',
          level: 95,
          message_on_trigger: '跌破95，观察是否转弱',
          action_on_trigger: '观望',
        },
      ],
    },
  }, 94).state,
  'near',
)
assert.equal(formatWatchPrice(40), '40')
assert.equal(formatWatchPrice(253.49), '253.49')

console.log('watchboardState tests passed')
