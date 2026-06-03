import { NextRequest, NextResponse } from 'next/server';
import { tradeService } from '@/service';

/**
 * GET /api/trades - 获取交易记录
 */
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const companyId = searchParams.get('companyId');

    let trades;
    if (companyId) {
      trades = await tradeService.getTradesByCompanyId(companyId);
    } else {
      trades = await tradeService.getAllTrades();
    }

    return NextResponse.json({ success: true, data: trades });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '获取交易记录失败' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/trades - 创建交易记录
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const trade = await tradeService.createTrade(body);
    return NextResponse.json({ success: true, data: trade }, { status: 201 });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '创建交易记录失败' },
      { status: 500 }
    );
  }
}
