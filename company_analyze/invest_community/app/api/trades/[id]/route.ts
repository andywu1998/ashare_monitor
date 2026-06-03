import { NextRequest, NextResponse } from 'next/server';
import { tradeService } from '@/service';

/**
 * PUT /api/trades/[id] - 更新交易记录
 */
export async function PUT(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    const body = await request.json();
    const trade = await tradeService.updateTrade(params.id, body);
    return NextResponse.json({ success: true, data: trade });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '更新交易记录失败' },
      { status: 500 }
    );
  }
}

/**
 * DELETE /api/trades/[id] - 删除交易记录
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    await tradeService.deleteTrade(params.id);
    return NextResponse.json({ success: true, message: '删除成功' });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '删除交易记录失败' },
      { status: 500 }
    );
  }
}
