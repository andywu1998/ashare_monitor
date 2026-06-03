import { BaseService } from './base.service';
import { prisma } from '@/lib/prisma';
import { Event } from '@/lib/types';

/**
 * 事件管理服务 - 完整实现
 */
export class EventService extends BaseService {
  private static instance: EventService;

  private constructor() {
    super();
  }

  /**
   * 获取单例实例
   */
  static getInstance(): EventService {
    if (!EventService.instance) {
      EventService.instance = new EventService();
    }
    return EventService.instance;
  }

  /**
   * 获取所有事件
   */
  async getAllEvents(): Promise<Event[]> {
    try {
      const events = await prisma.event.findMany({
        include: {
          company: {
            select: {
              id: true,
              code: true,
              name: true,
              market: true
            }
          }
        },
        orderBy: { eventDate: 'desc' }
      });
      return events as any;
    } catch (error) {
      this.handleError(error, '获取事件列表失败');
    }
  }

  /**
   * 根据公司ID获取事件
   */
  async getEventsByCompanyId(companyId: string): Promise<Event[]> {
    try {
      const events = await prisma.event.findMany({
        where: { companyId },
        include: {
          company: {
            select: {
              code: true,
              name: true
            }
          }
        },
        orderBy: { eventDate: 'desc' }
      });
      return events as any;
    } catch (error) {
      this.handleError(error, '获取公司事件失败');
    }
  }

  /**
   * 根据日期范围获取事件
   */
  async getEventsByDateRange(startDate: Date, endDate: Date): Promise<Event[]> {
    try {
      const events = await prisma.event.findMany({
        where: {
          eventDate: {
            gte: startDate,
            lte: endDate
          }
        },
        include: {
          company: {
            select: {
              id: true,
              code: true,
              name: true
            }
          }
        },
        orderBy: { eventDate: 'asc' }
      });
      return events as any;
    } catch (error) {
      this.handleError(error, '获取日期范围事件失败');
    }
  }

  /**
   * 根据事件类型获取事件
   */
  async getEventsByType(eventType: string): Promise<Event[]> {
    try {
      const events = await prisma.event.findMany({
        where: { eventType },
        include: {
          company: {
            select: {
              code: true,
              name: true
            }
          }
        },
        orderBy: { eventDate: 'desc' }
      });
      return events as any;
    } catch (error) {
      this.handleError(error, '获取事件类型失败');
    }
  }

  /**
   * 创建事件
   */
  async createEvent(data: {
    companyId: string;
    title: string;
    description?: string;
    eventDate: Date | string;
    eventType: string;
    impact?: string;
    source?: string;
  }): Promise<Event> {
    try {
      // 验证公司是否存在
      const company = await prisma.company.findUnique({
        where: { id: data.companyId }
      });

      if (!company) {
        throw new Error('公司不存在');
      }

      const event = await prisma.event.create({
        data: {
          companyId: data.companyId,
          title: data.title,
          description: data.description,
          eventDate: new Date(data.eventDate),
          eventType: data.eventType,
          impact: data.impact,
          source: data.source
        },
        include: {
          company: {
            select: {
              code: true,
              name: true
            }
          }
        }
      });
      return event as any;
    } catch (error) {
      this.handleError(error, '创建事件失败');
    }
  }

  /**
   * 更新事件
   */
  async updateEvent(id: string, data: {
    title?: string;
    description?: string;
    eventDate?: Date | string;
    eventType?: string;
    impact?: string;
    source?: string;
  }): Promise<Event> {
    try {
      const updateData: any = { ...data };

      if (data.eventDate) {
        updateData.eventDate = new Date(data.eventDate);
      }

      updateData.updatedAt = new Date();

      const event = await prisma.event.update({
        where: { id },
        data: updateData,
        include: {
          company: {
            select: {
              code: true,
              name: true
            }
          }
        }
      });
      return event as any;
    } catch (error) {
      this.handleError(error, '更新事件失败');
    }
  }

  /**
   * 删除事件
   */
  async deleteEvent(id: string): Promise<void> {
    try {
      await prisma.event.delete({
        where: { id }
      });
    } catch (error) {
      this.handleError(error, '删除事件失败');
    }
  }

  /**
   * 获取即将发生的事件
   */
  async getUpcomingEvents(days: number = 30): Promise<Event[]> {
    try {
      const now = new Date();
      const futureDate = new Date();
      futureDate.setDate(now.getDate() + days);

      const events = await prisma.event.findMany({
        where: {
          eventDate: {
            gte: now,
            lte: futureDate
          }
        },
        include: {
          company: {
            select: {
              code: true,
              name: true
            }
          }
        },
        orderBy: { eventDate: 'asc' }
      });
      return events as any;
    } catch (error) {
      this.handleError(error, '获取即将发生的事件失败');
    }
  }
}

export const eventService = EventService.getInstance();
