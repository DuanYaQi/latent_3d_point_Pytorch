import os
import os.path as osp
import time
import datetime
import warnings
import torch
import torch.nn as nn
import torch.nn.functional as F
from argparse import ArgumentParser
from torch.utils.data import TensorDataset,DataLoader
from torchkeras import Model,summary

from utils.in_out import snc_category_to_synth_id
from utils.dataset import ShapeNetDataset
from utils.plot_3d_pc import plot_3d_point_cloud
from metric.loss import ChamferLoss

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
# --------------------------------------------------------------------------------------print time
def printbar():
    nowtime = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print("\n"+"=========="*8 + "%s"%nowtime)

# --------------------------------------------------------------------------------------AE
class EncoderDecoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv1d(3, 64, 1)
        self.conv2 = nn.Conv1d(64, 128, 1)
        self.conv3 = nn.Conv1d(128, 128, 1)
        self.conv4 = nn.Conv1d(128, 256, 1)
        self.conv5 = nn.Conv1d(256, 128, 1)
        self.bn1 = nn.BatchNorm1d(64)
        self.bn2 = nn.BatchNorm1d(128)
        self.bn3 = nn.BatchNorm1d(128)
        self.bn4 = nn.BatchNorm1d(256)
        self.bn5 = nn.BatchNorm1d(128)
        self.relu = nn.ReLU()

        self.fc1 = nn.Linear(128, 256)
        self.fc2 = nn.Linear(256, 256)
        self.fc3 = nn.Linear(256, 6144)

    
    def forward(self,x):
        batchsize = x.size()[0]
        pointnum = x.size()[1]
        channel = x.size()[2]
        z = x.transpose(2, 1)
        z = F.relu(self.bn1(self.conv1(z)))
        z = F.relu(self.bn2(self.conv2(z)))
        z = F.relu(self.bn3(self.conv3(z)))
        z = F.relu(self.bn4(self.conv4(z)))
        z = F.relu(self.bn5(self.conv5(z)))
        z = torch.max(z, 2, keepdim=True)[0]
        z = z.view(-1, 128)

        z = F.relu(self.fc1(z))
        z = F.relu(self.fc2(z))
        z = self.fc3(z)

        z = z.view(-1, channel, pointnum)
        z = z.transpose(2, 1)
        return z

    def loss_func(self, z, x):  
        loss = ChamferLoss()
        cd = loss(z,x)
        return cd

    @property
    def optimizer(self):
        return torch.optim.Adam(self.parameters(),lr = 0.0005)

# -----------------------------------------------------------------------------------------
def train_step(model, features):
    # train，dropout work/ valid dropout dont work
    model.train()

    # forward to loss
    predictions = model(features)
    loss = model.loss_func(predictions,features)
    
    # backward to gradient
    loss.backward()
    
    # update model params  and  zero_grads
    model.optimizer.step()
    model.optimizer.zero_grad()
    
    return loss.item()

# -----------------------------------------------------------------------------------------
def train_model(model, dataloader, epochs):
    for epoch in range(1,epochs+1):
        for features in dataloader:
            loss = train_step(model,features.to(device))
        if epoch%1==0:
            printbar()
            print("epoch =",epoch,"loss = ",loss)

# -----------------------------------------------------------------------------------------
def showfig(model, dataloader):
    feed_pc = next(iter(dataloader))
    reconstructions = model(feed_pc.to(device))
    if torch.cuda.is_available():
        reconstructions = reconstructions.detach().to("cpu")
    else:
        reconstructions = reconstructions.detach()
    
    i = 49
    # Ground Truth
    plot_3d_point_cloud(feed_pc[i][:, 0], 
                        feed_pc[i][:, 1], 
                        feed_pc[i][:, 2], in_u_sphere=True);
    # Generative Point
    plot_3d_point_cloud(reconstructions[i][:, 0], 
                        reconstructions[i][:, 1], 
                        reconstructions[i][:, 2], in_u_sphere=True);

# -----------------------------------------------------------------------------------------
def parse_arguments():
    parser = ArgumentParser()
    parser.add_argument('--top_in_dir', type=str, help='Top-dir of where point-clouds are stored', default = '/home/latent_3d_points_Pytorch/data/shape_net_core_uniform_samples_2048/')
    parser.add_argument('--n_pc_points', type=int, help='Number of points per model', default = 2048)       #TODO: Adapt datasets
    parser.add_argument('--bneck_size', type=int, help='Bottleneck-AE size', default = 128)                 #TODO: Adapt haparms
    parser.add_argument('--ae_loss', type=str, help='Loss to optimize: emd or chamfer', default = 'chamfer') #TODO: ADD EMD
    parser.add_argument('--class_name', type=str, default = 'chair')
    parser.add_argument('--batch_size', type=int, default = 50)
    parser.add_argument('--sample_num', type=int, default = 6000)
    parser.add_argument('--epochs', type=int, default = 50)
    return parser.parse_args()

# -----------------------------------------------------------------------------------------
def train(phase='Train', checkpoint_path: str=None, show: bool=False, verbose: bool=False):
    args = parse_arguments()

    # Load Point-Clouds
    syn_id = snc_category_to_synth_id()[args.class_name]  # class2id
    class_dir = osp.join(args.top_in_dir , syn_id)

    dataset = ShapeNetDataset(samples_dir = class_dir, sample_num = args.sample_num)
    dataloader = DataLoader(dataset, batch_size = args.batch_size, shuffle=False, num_workers=2)
    model = EncoderDecoder()
    #summary(model,input_shape= (2048,3))
    model = model.to(device)

    if phase == 'Train':
        if not(verbose):
            train_model(model, dataloader, args.epochs)
        else:
            tic = time.time()
            train_model(model, dataloader, args.epochs)
            toc = time.time()
            print("time used:",toc-tic,'s')
        if checkpoint_path is not None:
            torch.save(model.state_dict(), checkpoint_path)
            print(f'Model has been save to \033[1m{checkpoint_path}\033[0m')
    elif phase == 'continueTrain':
        model.load_state_dict(torch.load(checkpoint_path))
        train_model(model, dataloader, args.epochs)
        if checkpoint_path is not None:
            torch.save(model.state_dict(), checkpoint_path)
            print(f'Model has been save to \033[1m{checkpoint_path}\033[0m')
    else:
        model.load_state_dict(torch.load(checkpoint_path))

    if show:
        showfig(model, dataloader)

# -----------------------------------------------------------------------------------------
if __name__ == "__main__":
    checkpoint_path = './model/AEModel.pkl'
    show = True
    verbose = True
    train('Test', checkpoint_path, show, verbose)
    #train('Test', checkpoint_path)
