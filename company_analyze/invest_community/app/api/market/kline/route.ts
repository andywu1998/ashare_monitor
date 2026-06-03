import { NextRequest, NextResponse } from 'next/server';
import { marketService } from '@/service';

/**
 * GET /api/market/kline - 获取K线数据
 */
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const symbol = searchParams.get('symbol');
    const interval = searchParams.get('interval') || '1d';
    const startTime = searchParams.get('startTime');
    const endTime = searchParams.get('endTime');

    if (!symbol) {
      return NextResponse.json(
        { success: false, error: '缺少symbol参数' },
        { status: 400 }
      );
    }

    const klineData = await marketService.getKlineData(
      symbol,
      interval,
      startTime ? parseInt(startTime) : undefined,
      endTime ? parseInt(endTime) : undefined
    );

    return NextResponse.json({ success: true, data: klineData });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '获取K线数据失败' },
      { status: 500 }
    );
  }
}
