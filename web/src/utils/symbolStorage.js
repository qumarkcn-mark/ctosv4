const DEFAULT_SYMBOL = 'sh600519'
const DEFAULT_SYMBOL_NAME = '贵州茅台'

function parseStoredSymbol(rawSymbol, rawName) {
  if (!rawSymbol) {
    return { symbol: DEFAULT_SYMBOL, name: rawName || DEFAULT_SYMBOL_NAME }
  }

  try {
    const parsed = JSON.parse(rawSymbol)
    if (parsed && typeof parsed === 'object') {
      const symbol = parsed.symbol || parsed.code || DEFAULT_SYMBOL
      const name = rawName || parsed.name || symbol
      return { symbol, name }
    }
  } catch {
    // 非 JSON 的正常股票代码会走下面的字符串分支。
  }

  return { symbol: rawSymbol, name: rawName || rawSymbol }
}

export function readLastViewedSymbol() {
  const rawSymbol = localStorage.getItem('lastViewedSymbol')
  const rawName = localStorage.getItem('lastViewedSymbolName')
  const record = parseStoredSymbol(rawSymbol, rawName)

  if (rawSymbol !== record.symbol || rawName !== record.name) {
    localStorage.setItem('lastViewedSymbol', record.symbol)
    localStorage.setItem('lastViewedSymbolName', record.name)
  }

  return record
}

export function normalizeSymbolInput(symbol, name) {
  if (symbol && typeof symbol === 'object') {
    const nextSymbol = symbol.symbol || symbol.code || DEFAULT_SYMBOL
    return {
      symbol: nextSymbol,
      name: name || symbol.name || nextSymbol,
    }
  }
  const nextSymbol = symbol || DEFAULT_SYMBOL
  return {
    symbol: nextSymbol,
    name: name || nextSymbol,
  }
}
