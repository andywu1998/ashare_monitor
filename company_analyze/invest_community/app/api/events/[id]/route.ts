import { NextRequest, NextResponse } from 'next/server';
import { eventService } from '@/service';

/**
 * PUT /api/events/[id] - 更新事件
 */
export async function PUT(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    const body = await request.json();
    const event = await eventService.updateEvent(params.id, body);
    return NextResponse.json({ success: true, data: event });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '更新事件失败' },
      { status: 500 }
    );
  }
}

/**
 * DELETE /api/events/[id] - 删除事件
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    await eventService.deleteEvent(params.id);
    return NextResponse.json({ success: true, message: '删除成功' });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '删除事件失败' },
      { status: 500 }
    );
  }
}
