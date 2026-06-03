import { NextRequest, NextResponse } from 'next/server';
import { eventService } from '@/service';

/**
 * GET /api/events - 获取所有事件
 */
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const companyId = searchParams.get('companyId');
    const startDate = searchParams.get('startDate');
    const endDate = searchParams.get('endDate');

    let events;
    if (companyId) {
      events = await eventService.getEventsByCompanyId(companyId);
    } else if (startDate && endDate) {
      events = await eventService.getEventsByDateRange(
        new Date(startDate),
        new Date(endDate)
      );
    } else {
      events = await eventService.getAllEvents();
    }

    return NextResponse.json({ success: true, data: events });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '获取事件列表失败' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/events - 创建事件
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const event = await eventService.createEvent(body);
    return NextResponse.json({ success: true, data: event }, { status: 201 });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : '创建事件失败' },
      { status: 500 }
    );
  }
}
