import { BaseService } from './base.service';
import { prisma } from '@/lib/prisma';
import { Company } from '@/lib/types';

/**
 * 公司/标的管理服务 - 完整实现
 */
export class CompanyService extends BaseService {
  private static instance: CompanyService;

  private constructor() {
    super();
  }

  /**
   * 获取单例实例
   */
  static getInstance(): CompanyService {
    if (!CompanyService.instance) {
      CompanyService.instance = new CompanyService();
    }
    return CompanyService.instance;
  }

  /**
   * 获取所有公司列表
   */
  async getAllCompanies(): Promise<Company[]> {
    try {
      const companies = await prisma.company.findMany({
        orderBy: { createdAt: 'desc' },
        include: {
          _count: {
            select: {
              events: true,
              trades: true,
              klines: true
            }
          }
        }
      });
      return companies as any;
    } catch (error) {
      this.handleError(error, '获取公司列表失败');
    }
  }

  /**
   * 根据ID获取公司详情
   */
  async getCompanyById(id: string): Promise<Company | null> {
    try {
      const company = await prisma.company.findUnique({
        where: { id },
        include: {
          events: {
            orderBy: { eventDate: 'desc' },
            take: 10
          },
          trades: {
            orderBy: { tradeDate: 'desc' },
            take: 10
          }
        }
      });
      return company as any;
    } catch (error) {
      this.handleError(error, '获取公司详情失败');
    }
  }

  /**
   * 根据股票代码获取公司
   */
  async getCompanyByCode(code: string): Promise<Company | null> {
    try {
      const company = await prisma.company.findUnique({
        where: { code }
      });
      return company as any;
    } catch (error) {
      this.handleError(error, '获取公司信息失败');
    }
  }

  /**
   * 创建公司
   */
  async createCompany(data: {
    code: string;
    name: string;
    market: string;
    industry?: string;
    description?: string;
  }): Promise<Company> {
    try {
      // 检查股票代码是否已存在
      const existing = await prisma.company.findUnique({
        where: { code: data.code }
      });

      if (existing) {
        throw new Error(`股票代码 ${data.code} 已存在`);
      }

      const company = await prisma.company.create({
        data: {
          code: data.code,
          name: data.name,
          market: data.market,
          industry: data.industry,
          description: data.description
        }
      });
      return company as any;
    } catch (error) {
      this.handleError(error, '创建公司失败');
    }
  }

  /**
   * 更新公司信息
   */
  async updateCompany(id: string, data: {
    name?: string;
    market?: string;
    industry?: string;
    description?: string;
  }): Promise<Company> {
    try {
      const company = await prisma.company.update({
        where: { id },
        data: {
          name: data.name,
          market: data.market,
          industry: data.industry,
          description: data.description,
          updatedAt: new Date()
        }
      });
      return company as any;
    } catch (error) {
      this.handleError(error, '更新公司信息失败');
    }
  }

  /**
   * 删除公司
   */
  async deleteCompany(id: string): Promise<void> {
    try {
      await prisma.company.delete({
        where: { id }
      });
    } catch (error) {
      this.handleError(error, '删除公司失败');
    }
  }

  /**
   * 搜索公司
   */
  async searchCompanies(keyword: string): Promise<Company[]> {
    try {
      const companies = await prisma.company.findMany({
        where: {
          OR: [
            { code: { contains: keyword, mode: 'insensitive' } },
            { name: { contains: keyword, mode: 'insensitive' } }
          ]
        },
        orderBy: { createdAt: 'desc' }
      });
      return companies as any;
    } catch (error) {
      this.handleError(error, '搜索公司失败');
    }
  }

  /**
   * 根据市场筛选公司
   */
  async getCompaniesByMarket(market: string): Promise<Company[]> {
    try {
      const companies = await prisma.company.findMany({
        where: { market },
        orderBy: { createdAt: 'desc' }
      });
      return companies as any;
    } catch (error) {
      this.handleError(error, '获取市场公司失败');
    }
  }

  /**
   * 根据行业筛选公司
   */
  async getCompaniesByIndustry(industry: string): Promise<Company[]> {
    try {
      const companies = await prisma.company.findMany({
        where: { industry },
        orderBy: { createdAt: 'desc' }
      });
      return companies as any;
    } catch (error) {
      this.handleError(error, '获取行业公司失败');
    }
  }
}

export const companyService = CompanyService.getInstance();
