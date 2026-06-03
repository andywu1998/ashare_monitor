import { NextRequest, NextResponse } from 'next/server';
import { marketService } from '@/service';

/**
 * GET /api/market/quote - 获取实时行情
 */
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const symbol = searchParams.get('symbol');
    const symbols = searchParams.get('symbols');

    if (symbols) {
      // 批量获取
      const symbolList = symbols.split(',');
      const quotes = await marketService.getBatchQuotes(symbolList);
      return NextResponse.json({ success: true, data: quotes });
    } else if (symbol) {
      // 单个获取
      const quote = await marketService.getRealtimeQuote(symbol);
      return NextResponse.json({ success: true, data: quote });
    } else {
      return NextResponse.json(
        { success: false, error: '缺少symbol或symbols参数' },
        { status: 400 }
      );
    }
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '获取行情数据失败' },
      { status: 500 }
    );
  }
}
